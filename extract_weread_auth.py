#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_weread_auth.py — 从 KOReader 的 weread.lua 提取微信读书登录凭证

背景: weread.koplugin 登录后, 凭证保存在 KOReader 设备上的
      koreader/settings/weread.lua (LuaSettings 格式, 可能是 Lua 表或 JSON),
      内含 cookies 表(含 wr_vid / wr_gid 或 wr_skey) 和 api_key。

用法:
  python extract_weread_auth.py <weread.lua路径>
      # 打印 Cookie 头字符串 和 API Key, 可直接粘贴给下载脚本

  也可以配合下载脚本一步到位:
  python weread_pc_downloader.py --book-url <链接> --auth-file <weread.lua路径> --out-dir ./weread_cache
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _find_block(text: str, key: str) -> str:
    """取顶层键 key 对应的值块(到下一个顶层键/末尾为止)。
    兼容 LuaSettings 两种写法: ["cookies"] = {...} 和 cookies = {...}
    """
    pattern = re.compile(
        r"(?:^|\n)\s*(?:\[\"?" + re.escape(key) + r"\"?\]|" + re.escape(key) + r")\s*=",
        re.M,
    )
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    depth = 0
    in_string = False
    escape = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth <= 0:
                    return text[start:i]
            elif ch == "\n" and depth == 0:
                return text[start:i]
        i += 1
    return text[start:i]


def _kv_pairs(block: str) -> dict[str, str]:
    """兼容 ["name"] = "value" 和 name = "value" 两种写法。"""
    pairs: dict[str, str] = {}
    for match in re.finditer(r'\[?"?(\w+)"?\]?\s*=\s*"([^"]*)"', block):
        pairs[match.group(1)] = match.group(2)
    return pairs


def extract_auth(path: Path) -> dict[str, str]:
    raw = path.read_text(encoding="utf-8", errors="replace")

    # 先尝试 JSON(新版 LuaSettings json 后端)
    try:
        data = json.loads(raw)
        cookies = data.get("cookies") or {}
        if isinstance(cookies, dict):
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items() if isinstance(v, str))
            return {
                "cookie": cookie_str,
                "api_key": str(data.get("api_key") or ""),
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Lua 表格式: 取 cookies = { ... } 块
    cookie_block = _find_block(raw, "cookies")
    cookie_pairs = _kv_pairs(cookie_block)

    # api_key 可能是顶层字符串(两种写法)
    api_key = ""
    api_match = re.search(r'\[?"?api_key"?\]?\s*=\s*"([^"]+)"', raw)
    if api_match:
        api_key = api_match.group(1)

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_pairs.items())
    return {"cookie": cookie_str, "api_key": api_key}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("auth_file", type=Path, help="KOReader 设备上的 weread.lua 路径")
    args = ap.parse_args()

    if not args.auth_file.exists():
        sys.exit(f"文件不存在: {args.auth_file}")

    result = extract_auth(args.auth_file)
    if not result["cookie"]:
        sys.exit("没有解析出 cookies, 该文件可能不是 weread.koplugin 的凭证文件")

    print("Cookie 头字符串(复制这一整行, 用于 --cookie-string):")
    print(result["cookie"])
    print()
    if result["api_key"]:
        print("API Key(如脚本需要可存为环境变量 WEREAD_API_KEY):")
        print(result["api_key"])
    else:
        print("(未发现 API Key, 不影响下载)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
