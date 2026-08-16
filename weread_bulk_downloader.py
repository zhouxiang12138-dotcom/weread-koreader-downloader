#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weread_bulk_downloader.py — 微信读书书籍批量下载器(限流重试 + 断点续传 + 划线/想法注入)
(限流重试 + 断点续传 + 划线/想法注入)

基于 weread.koplugin 的协议实现, 针对大长篇优化:
- 每章失败自动重试(指数等待, 应对 -10102 限流)
- 每章内容落盘 .parts/, 中断后重跑自动续传
- 有 API Key 时, 自动拉取微信读书划线(/book/underlines)与想法(/book/readreviews),
  按插件 annotations.lua 的逻辑注入 <span class="wr-underline"> 与想法链接
- 幂等: 已注入划线的章节跳过重复注入

用法:
  python weread_bulk_downloader.py --book-id 26150754 \
      --cookie-string "<cookies>" --api-key "wrk-..." \
      --out-dir ./weread_cache [--limit 100] [--sleep 0.4]

  # 只给已下载章节补注划线(不重下正文): 重跑同一条命令即可
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
FETCH_SCRIPT = next(
    (c for c in (
        _SCRIPT_DIR / "fetch_weread_epub.py",
        _SCRIPT_DIR / "weread.koplugin" / "scripts" / "fetch_weread_epub.py",
    ) if c.exists()),
    _SCRIPT_DIR / "fetch_weread_epub.py",
)

UNDERLINE_CSS = ".wr-underline {\n    border-bottom: 2px dashed #ff6b35;\n    padding-bottom: 2px;\n}\n"
THOUGHT_LINK_CSS = ".wr-thought-link{text-decoration:none;color:inherit;}\n"

import html as _html


def load_fetch():
    spec = importlib.util.spec_from_file_location("fetch_weread_epub", FETCH_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_weread_epub"] = mod
    spec.loader.exec_module(mod)
    return mod


def gateway(api_key: str, api_name: str, params: dict) -> dict:
    """调用微信读书 Agent Gateway(直连, 不走系统代理)。"""
    body = {"api_name": api_name, "skill_version": "1.0.3", **params}
    req = urllib.request.Request(
        "https://i.weread.qq.com/api/agent/gateway",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"gateway {api_name} errcode={data.get('errcode')} {data.get('errmsg')}")
    return data


def id_safe(text) -> str:
    return re.sub(r"[^\w.\-]", "_", str(text or ""))


def thought_anchor_id(book_id, chapter_uid, range_str) -> str:
    return f"wrthought-{id_safe(book_id)}-{id_safe(chapter_uid)}-{range_str}"


def parse_range(range_str) -> tuple[int, int] | None:
    m = re.match(r"^(\d+)-(\d+)$", str(range_str or ""))
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if start >= end:
        return None
    return start, end  # [start, end) 半开, 与插件 Lua 转换后一致


def snap_end_to_safe(runes, start, end):
    n = len(runes)
    if end <= start or end > n:
        return end
    for i in range(end - 1, start - 1, -1):
        if runes[i] == ">":
            break
        if runes[i] == "<":
            return i
    for i in range(end - 1, start - 1, -1):
        if i < end - 12:
            break
        r = runes[i]
        if r in ";<>":
            break
        if r == "&":
            return i
    return end


def snap_start_to_safe(runes, start, end):
    n = len(runes)
    if start < 0 or start >= end or start >= n:
        return start
    for i in range(start - 1, -1, -1):
        if i < start - 200:
            break
        if runes[i] == ">":
            break
        if runes[i] == "<":
            for j in range(start, n):
                if runes[j] == ">":
                    return j + 1
            break
    for i in range(start - 1, -1, -1):
        if i < start - 12:
            break
        r = runes[i]
        if r in ";<>":
            break
        if r == "&":
            for j in range(start, min(start + 12, n)):
                if runes[j] == ";":
                    return j + 1
            break
    return start


def wrap_text_segments(inner, class_name):
    open_tag = f'<span class="{class_name}">'
    close_tag = "</span>"
    result = []
    text_buf = []
    in_tag = False

    def wrap_segment(seg):
        if not seg:
            return
        has_content = any(not ch.isspace() for ch in seg)
        if has_content:
            result.append(open_tag)
            result.extend(seg)
            result.append(close_tag)
        else:
            result.extend(seg)

    for r in inner:
        if r == "<":
            if text_buf:
                wrap_segment(text_buf)
                text_buf = []
            in_tag = True
            result.append(r)
        elif r == ">":
            in_tag = False
            result.append(r)
        elif in_tag:
            result.append(r)
        else:
            text_buf.append(r)
    if text_buf:
        wrap_segment(text_buf)
    return result


def inject_underlines(html, underlines, thought_map, chapter_uid, book_id):
    """移植自 weread.koplugin annotations.lua 的 injectUnderlines。"""
    if not html or not underlines:
        return html

    html = html.lstrip("\ufeff")

    ranges = []
    for ul in underlines:
        pr = parse_range(ul.get("range"))
        if pr:
            ranges.append({"range_str": ul["range"], "start": pr[0], "end": pr[1]})
    if not ranges:
        return html

    ranges.sort(key=lambda r: r["start"])
    runes = list(html)
    n = len(runes)
    replacements = []
    prev_end = 0

    for ul in ranges:
        start_pos, end_pos = ul["start"], ul["end"]
        if start_pos < 0 or end_pos > n or start_pos >= end_pos:
            continue
        end_pos = snap_end_to_safe(runes, start_pos, end_pos)
        start_pos = snap_start_to_safe(runes, start_pos, end_pos)
        if start_pos >= end_pos or start_pos < prev_end:
            continue

        inner = runes[start_pos:end_pos]
        wrapped = wrap_text_segments(inner, "wr-underline")

        if thought_map and thought_map.get(ul["range_str"]):
            open_tag = '<span class="wr-underline">'
            close_tag = "</span>"
            anchor_id = thought_anchor_id(book_id, chapter_uid, ul["range_str"])
            href = "#" + anchor_id
            open_a = f'<a class="wr-thought-link" href="{href}">'
            open_a_id = f'<a id="{anchor_id}" class="wr-thought-link" href="{href}">'
            with_links = []
            first = True
            for item in wrapped:
                if item == open_tag:
                    with_links.append(open_a_id if first else open_a)
                    first = False
                    with_links.append(item)
                elif item == close_tag:
                    with_links.append(item)
                    with_links.append("</a>")
                else:
                    with_links.append(item)
            wrapped = with_links

        replacements.append({"start": start_pos, "end": end_pos, "content": wrapped})
        prev_end = end_pos

    if not replacements:
        return html

    result = []
    prev = 0
    for rep in replacements:
        result.extend(runes[prev:rep["start"]])
        result.extend(rep["content"])
        prev = rep["end"]
    result.extend(runes[prev:])
    return "".join(result)


def fetch_underlines(api_key, book_id, chapter_uid) -> list[dict]:
    data = gateway(api_key, "/book/underlines",
                   {"bookId": str(book_id), "chapterUid": chapter_uid})
    return data.get("underlines") or []


def fetch_reviews(api_key, book_id, chapter_uid, ranges: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ranges), 5):
        batch = [{"range": r, "maxIdx": 0, "count": 30, "synckey": 0} for r in ranges[i:i + 5]]
        data = gateway(api_key, "/book/readreviews",
                       {"bookId": str(book_id), "chapterUid": chapter_uid, "reviews": batch})
        out.extend(data.get("reviews") or [])
    return out


def annotate_chapter(api_key, book_id, chapter_uid, content: str) -> tuple[str, str]:
    """拉取划线+想法并注入 HTML。返回 (新html, 附加css)。失败返回 (原html, "")。"""
    if not api_key or not content:
        return content, ""
    try:
        underlines = fetch_underlines(api_key, book_id, chapter_uid)
        if not underlines:
            return content, ""
        ranges = [ul["range"] for ul in underlines if ul.get("range")]
        thought_map = {}
        if ranges:
            try:
                reviews = fetch_reviews(api_key, book_id, chapter_uid, ranges)
                for rv in reviews:
                    if rv.get("range") and rv.get("pageReviews"):
                        thought_map[rv["range"]] = True
            except Exception:
                thought_map = {}
        processed = inject_underlines(content, underlines, thought_map, chapter_uid, book_id)
        if processed != content:
            css = UNDERLINE_CSS
            if thought_map:
                css += "\n" + THOUGHT_LINK_CSS
            return processed, css
    except Exception as exc:
        print(f"    (划线注入跳过: {str(exc)[:100]})", flush=True)
    return content, ""


def html_escape_keep_tags(s: str) -> str:
    """只转义文本字符, 保留 <span>/<a> 等注入标签原样。"""
    parts = re.split(r"(<[^>]*>)", s)
    return "".join(
        part if (part.startswith("<") and part.endswith(">")) else _html.escape(part, quote=False)
        for part in parts
    )


def inject_txt_segment(seg: str, local_ranges: list, book_id, uid) -> str:
    """在单个纯文本段内注入划线 span(不跨段, 无偏移)。local_ranges: [(start,end,range_str,has_thought)]"""
    runes = list(seg)
    n = len(runes)
    repl = []
    prev = 0
    for start, end, range_str, has_thought in sorted(local_ranges, key=lambda x: x[0]):
        start = max(0, start)
        end = min(n, end)
        if start >= end or start < prev:
            continue
        inner = runes[start:end]
        wrapped = wrap_text_segments(inner, "wr-underline")
        if has_thought:
            open_tag = '<span class="wr-underline">'
            close_tag = "</span>"
            anchor_id = thought_anchor_id(book_id, uid, range_str)
            href = "#" + anchor_id
            open_a = f'<a class="wr-thought-link" href="{href}">'
            open_a_id = f'<a id="{anchor_id}" class="wr-thought-link" href="{href}">'
            wl = []
            first = True
            for item in wrapped:
                if item == open_tag:
                    wl.append(open_a_id if first else open_a)
                    first = False
                    wl.append(item)
                elif item == close_tag:
                    wl.append(item)
                    wl.append("</a>")
                else:
                    wl.append(item)
            wrapped = wl
        repl.append((start, end, wrapped))
        prev = end
    out = []
    p = 0
    for s, e, w in repl:
        out.extend(runes[p:s])
        out.extend(w)
        p = e
    out.extend(runes[p:])
    return html_escape_keep_tags("".join(out))


def build_txt_html(api_key, book_id, uid, plain, chapter_title) -> tuple[str, str]:
    """txt 章节 -> 注入划线的 XHTML。range 基于 plain 原文(含 \r\n), 按段注入。"""
    title_esc = _html.escape(str(chapter_title or ""), quote=True)
    empty = (f'<?xml version="1.0" encoding="utf-8"?>\n'
             f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title_esc}</title></head>\n'
             f"<body></body></html>")
    if not plain:
        return empty, ""

    lines = plain.split("\r\n")
    ann_css = ""
    seg_ranges: dict[int, list] = {}

    if api_key:
        try:
            uls = fetch_underlines(api_key, book_id, uid)
            ranges = [ul["range"] for ul in uls if ul.get("range")]
            thought_map: dict[str, bool] = {}
            if ranges:
                try:
                    for rv in fetch_reviews(api_key, book_id, uid, ranges):
                        if rv.get("range") and rv.get("pageReviews"):
                            thought_map[rv["range"]] = True
                except Exception:
                    thought_map = {}
            offsets = []
            cur = 0
            for line in lines:
                offsets.append(cur)
                cur += len(line) + 2  # \r\n
            for ul in uls:
                pr = parse_range(ul.get("range"))
                if not pr:
                    continue
                a, b = pr
                has_t = bool(thought_map.get(ul["range"]))
                for i, line in enumerate(lines):
                    seg_start = offsets[i]
                    seg_end = seg_start + len(line)
                    if seg_end <= a or seg_start >= b:
                        continue
                    la = max(a, seg_start) - seg_start
                    lb = min(b, seg_end) - seg_start
                    if lb <= la:
                        continue
                    seg_ranges.setdefault(i, []).append((la, lb, ul["range"], has_t))
            if seg_ranges:
                ann_css = UNDERLINE_CSS
                if thought_map:
                    ann_css += "\n" + THOUGHT_LINK_CSS
        except Exception as exc:
            print(f"    (txt划线注入跳过: {str(exc)[:100]})", flush=True)

    html_lines = []
    for i, line in enumerate(lines):
        if i in seg_ranges:
            html_lines.append(f"<p>{inject_txt_segment(line, seg_ranges[i], book_id, uid)}</p>")
        else:
            html_lines.append(f"<p>{_html.escape(line, quote=False)}</p>")

    xhtml = (f'<?xml version="1.0" encoding="utf-8"?>\n'
             f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title_esc}</title></head>\n'
             f"<body>\n" + "\n".join(html_lines) + "\n</body></html>")
    return xhtml, ann_css


def fetch_chapter_annotated(fetch, client, api_key, book_id, chapter, sleep_seconds,
                            content_format) -> tuple[str, str, str, list, str]:
    """fetch_chapter 的增强版: 在图片地址重写【之前】注入划线(与 KOReader 插件顺序一致)。
    返回 (title, content, css, assets, annotation_css)。
    """
    import time as _time
    chapter_uid = chapter["chapterUid"]
    title = chapter.get("title") or f"Chapter {chapter_uid}"
    referer = fetch.reader_url_for(book_id, chapter_uid)
    reader_html = client.get_text(referer, referer=referer)
    psvts = fetch.read_reader_state(reader_html).psvts
    if not psvts:
        raise ValueError(f"Missing psvts for chapterUid={chapter_uid}")

    def post(endpoint: str, *, style: bool = False) -> str:
        params = fetch.make_content_params(book_id, chapter_uid, psvts, style=style, sc=1)
        result = client.request(
            "https://weread.qq.com" + endpoint,
            method="POST",
            data=params,
            referer=referer,
        ).decode("utf-8", "replace")
        if result == "{}":
            raise ValueError(f"{endpoint} returned empty object for chapterUid={chapter_uid}")
        if sleep_seconds:
            _time.sleep(sleep_seconds)
        return result

    ann_css = ""
    if content_format[0] == "txt":
        t0 = post("/web/book/chapter/t_0")
        t1_text = ""
        try:
            t1_text = post("/web/book/chapter/t_1")
        except ValueError:
            pass
        plain = fetch.decode_content_shards(t0, t1_text, "")
        html, ann_css = build_txt_html(api_key, book_id, chapter_uid, plain, title)
        return str(title), html, "", [], ann_css

    e0 = post("/web/book/chapter/e_0")
    if e0.startswith("{") and '"bookId"' in e0:
        content_format[0] = "txt"
        t0 = post("/web/book/chapter/t_0")
        t1_text = ""
        try:
            t1_text = post("/web/book/chapter/t_1")
        except ValueError:
            pass
        plain = fetch.decode_content_shards(t0, t1_text, "")
        html, ann_css = build_txt_html(api_key, book_id, chapter_uid, plain, title)
        return str(title), html, "", [], ann_css

    content_format[0] = "epub"
    e1 = post("/web/book/chapter/e_1")
    e3 = post("/web/book/chapter/e_3")
    content = fetch.decode_content_shards(e0, e1, e3)
    css = ""
    try:
        e2 = post("/web/book/chapter/e_2", style=True)
        css = fetch.decode_style_shard(e2)
    except ValueError:
        pass

    # 划线注入: 必须在图片重写之前(range 基于原始 HTML)
    if api_key:
        content, ann_css = annotate_chapter(api_key, book_id, chapter_uid, content)

    assets, src_map = fetch.download_chapter_assets(client, chapter=chapter, referer=referer)
    content = fetch.rewrite_image_sources(content, src_map)
    return str(title), content, css, assets, ann_css


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--cookie-string", required=True, help="Cookie 头字符串(下载正文用)")
    ap.add_argument("--api-key", default="", help="API Key(划线/想法用, 可留空则跳过划线)")
    ap.add_argument("--out-dir", type=Path, default=Path("weread_cache"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--max-retries", type=int, default=6)
    ap.add_argument("--retry-delay", type=int, default=30)
    args = ap.parse_args()

    fetch = load_fetch()

    book_dir = args.out_dir / str(args.book_id)
    parts_dir = book_dir / ".parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = parts_dir / "manifest.json"

    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    done_uids: set[int] = set(int(u) for u in manifest.get("chapters", []))
    annotated_uids: set[int] = set(int(u) for u in manifest.get("annotated", []))

    client = fetch.WeReadClient(cookie_file=None, cookie_string=args.cookie_string, save_cookies=None)
    first_url = fetch.reader_url_for(args.book_id)
    reader_html = client.get_text(first_url, referer=first_url)
    state = fetch.read_reader_state(reader_html)
    book_id = state.book_id
    print(f"[*] bookId={book_id} title={state.book_title}", flush=True)

    cat = client.post_json("https://weread.qq.com/web/book/chapterInfos",
                           {"bookIds": [book_id]}, referer=first_url)
    _item, chapters = fetch.normalize_chapter_infos(cat, book_id)
    chapters = [c for c in chapters
                if int(c.get("wordCount") or 0) > 0 and str(c.get("title") or "") != "封面"]
    if args.limit:
        chapters = chapters[: args.limit]
    print(f"[*] 计划 {len(chapters)} 章 | 已正文 {len(done_uids)} | 已注划线 {len(annotated_uids)}",
          flush=True)

    content_format: list[str] = ["auto"]
    failed: list[str] = []
    css_all = ""
    annotate_css_map: dict[int, str] = {}

    for idx, chapter in enumerate(chapters, 1):
        uid = int(chapter["chapterUid"])
        part_dir = parts_dir / str(uid)
        # 需要(重新)下载: 无正文, 或开启了划线但该章尚未按正确顺序注入
        if uid not in done_uids or (args.api_key and uid not in annotated_uids):
            ok = False
            for attempt in range(1, args.max_retries + 1):
                try:
                    title, content, css, assets, ann_css = fetch_chapter_annotated(
                        fetch, client, args.api_key, book_id, chapter,
                        args.sleep, content_format)
                    part_dir.mkdir(parents=True, exist_ok=True)
                    (part_dir / "chapter.html").write_text(content, encoding="utf-8")
                    (part_dir / "css.txt").write_text(css or "", encoding="utf-8")
                    asset_meta = []
                    for asset in assets:
                        target = part_dir / asset.href
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(asset.data)
                        asset_meta.append({"href": asset.href, "media_type": asset.media_type})
                    (part_dir / "assets.json").write_text(
                        json.dumps(asset_meta, ensure_ascii=False), encoding="utf-8")
                    done_uids.add(uid)
                    if ann_css:
                        annotate_css_map[uid] = ann_css
                        annotated_uids.add(uid)
                    print(f"[{idx}/{len(chapters)}] {'↓' if uid not in done_uids else '↻'}{title}", flush=True)
                    ok = True
                    break
                except Exception as exc:
                    print(f"  ! 第{idx}章(uid={uid}) 第{attempt}次失败: {str(exc)[:120]}", flush=True)
                    if attempt < args.max_retries:
                        time.sleep(args.retry_delay)
            if not ok:
                failed.append(str(uid))
                print(f"  !! 跳过 uid={uid}", flush=True)
                continue
        else:
            print(f"[{idx}/{len(chapters)}] ={chapter.get('title')}(已有正文+划线)", flush=True)

        # 增量保存断点
        manifest["chapters"] = sorted(int(u) for u in done_uids)
        manifest["annotated"] = sorted(int(u) for u in annotated_uids)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # 组装 EPUB
    assembled = []
    for chapter in chapters:
        uid = int(chapter["chapterUid"])
        part_dir = parts_dir / str(uid)
        if not (part_dir / "chapter.html").exists():
            continue
        content = (part_dir / "chapter.html").read_text(encoding="utf-8")
        css = (part_dir / "css.txt").read_text(encoding="utf-8")
        if css and not css_all:
            css_all = css
        if uid in annotate_css_map:
            css_all = css_all + "\n" + annotate_css_map[uid]
        assets = []
        try:
            asset_meta = json.loads((part_dir / "assets.json").read_text(encoding="utf-8"))
            for am in asset_meta:
                data = (part_dir / am["href"]).read_bytes()
                assets.append(fetch.EpubAsset(href=am["href"], media_type=am["media_type"], data=data))
        except Exception:
            pass
        assembled.append((chapter.get("title") or f"Chapter {uid}", content, assets))

    epub_path = book_dir / f"{book_id}.epub"
    fetch.write_epub(epub_path, title=state.book_title, author=state.author,
                     chapters=assembled, css=css_all)
    print(f"[*] 完成: {epub_path} ({len(assembled)} 章, 划线章节 {len(annotate_css_map)})", flush=True)
    if failed:
        print(f"[!] 未成功章节: {failed[:20]}", flush=True)
    print("[*] 重跑同一条命令可续传/补注划线", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
