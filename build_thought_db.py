#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_thought_db.py — 为已下载的书籍生成 KOReader 插件可用的 thoughts.db

KOReader 微信读书插件点击划线弹「想法」时, 从书目录下的 thoughts.db
(SQLite, 表 review_items) 读取, 而不是从 EPUB 内嵌内容读取。
本工具按插件的 thought_db.lua schema, 拉取每章划线下想法(/book/readreviews)
写入数据库, 使点击划线即可弹出想法弹窗。

用法:
  python build_thought_db.py --book-id 29664125 --api-key "wrk-xxx" --out-dir ./weread_cache
  # 生成 weread_cache/29664125/thoughts.db, 拷入设备时与 .epub 一起放入书目录
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

FETCH_SCRIPT = Path(__file__).resolve().parent / "weread.koplugin" / "scripts" / "fetch_weread_epub.py"
BULK = Path(__file__).resolve().parent / "weread_bulk_downloader.py"

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_items (
    chapter_uid INTEGER NOT NULL,
    range       TEXT    NOT NULL,
    item_index  INTEGER NOT NULL,
    abstract    TEXT,
    author      TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    likes_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chapter_uid, range, item_index)
) WITHOUT ROWID
"""


def load_bulk():
    spec = importlib.util.spec_from_file_location("weread_bulk_downloader", BULK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weread_bulk_downloader"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--api-key", required=True, help="API Key(划线/想法接口需要)")
    ap.add_argument("--out-dir", type=Path, default=Path("weread_cache"))
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 章(0=全部已下载章节)")
    ap.add_argument("--sleep", type=float, default=0.25, help="请求间隔秒")
    args = ap.parse_args()

    bulk = load_bulk()
    book_dir = args.out_dir / str(args.book_id)
    parts_dir = book_dir / ".parts"
    if not parts_dir.exists():
        sys.exit(f"未找到章节缓存: {parts_dir}")

    uids = sorted(int(d.name) for d in parts_dir.iterdir() if d.is_dir() and d.name.isdigit())
    if args.limit:
        uids = uids[: args.limit]
    if not uids:
        sys.exit("没有可处理的章节")
    print(f"[*] book={args.book_id} 处理 {len(uids)} 章", flush=True)

    db_path = book_dir / "thoughts.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(SCHEMA)

    total_rows = 0
    for idx, uid in enumerate(uids, 1):
        try:
            uls = bulk.fetch_underlines(args.api_key, args.book_id, uid)
            ranges = [ul["range"] for ul in uls if ul.get("range")]
            rows = 0
            if ranges:
                reviews = bulk.fetch_reviews(args.api_key, args.book_id, uid, ranges)
                conn.execute("DELETE FROM review_items WHERE chapter_uid=?", (uid,))
                for rv in reviews:
                    range_str = rv.get("range")
                    prs = rv.get("pageReviews") or []
                    for i, pr in enumerate(prs, 1):
                        review = pr.get("review") or {}
                        author = review.get("author") or {}
                        abstract = None
                        if i == 1:
                            abstract = review.get("abstract") or review.get("contextAbstract")
                        conn.execute(
                            "INSERT INTO review_items "
                            "(chapter_uid, range, item_index, abstract, author, content, likes_count) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (uid, range_str, i, abstract,
                             author.get("nick") or author.get("name") or "匿名",
                             review.get("content") or "",
                             pr.get("likesCount") or 0))
                        rows += 1
                conn.commit()
            total_rows += rows
            print(f"[{idx}/{len(uids)}] uid={uid} 想法 {rows} 条", flush=True)
            time.sleep(args.sleep)
        except Exception as exc:
            print(f"  ! uid={uid} 失败: {str(exc)[:120]}", flush=True)
            time.sleep(2)

    conn.close()
    size = db_path.stat().st_size // 1024
    print(f"[*] 完成: {db_path} ({size}KB, 共 {total_rows} 条想法)")
    print("[*] 拷入设备时, 将 thoughts.db 与 .epub 一起放入书缓存目录即可")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
