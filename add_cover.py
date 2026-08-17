#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_cover.py — 给下载的 EPUB 补上微信读书封面

用法:
  python add_cover.py --book-id 29664125 --api-key "wrk-xxx" --out-dir ./weread_cache
  # 自动从 /book/info 获取封面 URL, 下载并写入 weread_cache/<bookId>/<bookId>.epub
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

COVER_EXT_BY_MAGIC = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}


def gateway(api_key, api_name, params):
    body = {"api_name": api_name, "skill_version": "1.0.3", **params}
    req = urllib.request.Request(
        "https://i.weread.qq.com/api/agent/gateway",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_ext(data: bytes) -> str:
    for magic, ext in COVER_EXT_BY_MAGIC.items():
        if data.startswith(magic):
            return ext
    return ".jpg"


def add_cover(epub_path: Path, cover_url: str) -> bool:
    """下载封面并写入 EPUB 的 OPF 元数据。返回是否成功。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(cover_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with opener.open(req, timeout=30) as resp:
        cover_data = resp.read()
    if not cover_data:
        print("封面下载为空")
        return False

    ext = detect_ext(cover_data)
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                  "gif": "image/gif"}.get(ext.lstrip("."), "image/jpeg")
    cover_href = f"images/cover{ext}"

    # 备份原文件
    bak = epub_path.with_suffix(epub_path.suffix + ".bak")
    shutil.copy2(epub_path, bak)

    tmp = epub_path.with_suffix(".tmp.epub")
    with zipfile.ZipFile(epub_path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        opf_data = None
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("content.opf"):
                opf_data = data.decode("utf-8")
                # cover item(独立判断, 已存在则不重复添加)
                if 'id="cover-image"' not in opf_data and "</manifest>" in opf_data:
                    cover_item = (f'<item id="cover-image" href="{cover_href}" '
                                  f'media-type="{media_type}" properties="cover-image"/>')
                    opf_data = opf_data.replace("</manifest>", cover_item + "</manifest>")
                # cover meta(独立判断)
                add_meta = '<meta name="cover" content="cover-image"/>'
                if add_meta not in opf_data:
                    if "<metadata>" in opf_data:
                        opf_data = opf_data.replace("<metadata>", "<metadata>" + add_meta)
                    else:
                        opf_data = re.sub(r"<metadata[^>]*>",
                                          lambda m: m.group(0) + add_meta, opf_data, count=1)
                data = opf_data.encode("utf-8")
            zout.writestr(item, data)
        # 写入封面图片
        zout.writestr("OEBPS/" + cover_href, cover_data)
        # 若 OPF 没找到则报错
        if opf_data is None:
            print("未找到 content.opf")
            tmp.unlink(missing_ok=True)
            return False

    tmp.replace(epub_path)
    print(f"封面已写入: {epub_path.name} ({cover_href}, {len(cover_data)//1024}KB)")
    print(f"原文件备份: {bak.name}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path("weread_cache"))
    args = ap.parse_args()

    epub = args.out_dir / str(args.book_id) / f"{args.book_id}.epub"
    if not epub.exists():
        sys.exit(f"EPUB 不存在: {epub}")

    info = gateway(args.api_key, "/book/info", {"bookId": str(args.book_id)})
    cover_url = info.get("cover", "")
    if not cover_url:
        sys.exit("API 未返回封面 URL")
    print(f"封面: {cover_url}")

    if add_cover(epub, cover_url):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
