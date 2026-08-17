# AGENTS.md — AI 一键部署指引

本文件面向 **AI 代理**（Claude / CodeBuddy / Cursor 等）。当用户说"下载微信读书的书到 KOReader"、"用这个仓库部署"、"帮我下书"时，按本流程执行。目标是：**从拿到书的链接到设备可用，全程由 AI 完成**。

---

## 0. 项目是什么

电脑端批量下载微信读书书籍（正文 + 划线注入），产出 KOReader 微信读书插件（weread.koplugin）可识别的「微信读书缓存书籍」目录，解锁：阅读进度双向同步、阅读时长上报、划线高亮、点击划线弹想法。

**必须的配套仓库**：[finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)（协议层 + 设备端插件），clone 到**本仓库同级目录**；或把其 `scripts/fetch_weread_epub.py` 复制到本目录。

## 1. 环境检查（先做，失败即停）

```bash
python3 --version          # 需 3.10+
ls weread.koplugin/scripts/fetch_weread_epub.py || ls fetch_weread_epub.py   # 依赖存在
```

- 本工具脚本：`weread_bulk_downloader.py`、`add_cover.py`、`build_thought_db.py`、`extract_weread_auth.py`
- **网络**：微信读书是国内服务，请求必须**直连**。若运行环境有 HTTP(S)_PROXY 代理变量且连接失败（`WinError 10061` / `ERR_NO_SUPPORTED_PROXIES`），先 `unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy` 再执行。脚本内部已强制直连（`ProxyHandler({})`）。

## 2. 获取凭证（向用户要，或自动提取）

| 凭证 | 用途 | 获取方式 |
|------|------|---------|
| **Cookie**（`wr_vid` + `wr_gid` 或 `wr_skey` + `wr_rt`） | 下载正文 | A) 浏览器登录 weread.qq.com → F12 → Application → Cookies；B) 若用户在 KOReader 登录过，把设备上 `koreader/settings/weread.lua` 拷来，运行 `python extract_weread_auth.py weread.lua` 自动提取 |
| **API Key**（`wrk-` 开头） | 划线/想法接口 | 微信读书 App → 我 → 设置 → 微信读书 Skill → 获取 API Key |

> ⚠️ 凭证敏感：优先用环境变量 `WEREAD_COOKIE` / `WEREAD_API_KEY` 传给脚本，避免命令行明文。不要让用户把凭证粘贴进公开聊天。

## 3. 下载书（核心）

需要用户提供：**书的阅读页链接**（`https://weread.qq.com/web/reader/xxx`）或 **bookId**。

**bookId 解析**：从 reader URL 用以下方式拿（脚本 `--book-id` 参数直接用）：

```python
# 用 fetch_weread_epub 的 WeReadClient GET reader 页, 解析 window.__INITIAL_STATE__ 的 reader.bookInfo.bookId
```

**下载命令**：

```bash
export WEREAD_COOKIE="wr_vid=xxx; wr_gid=yyy"
export WEREAD_API_KEY="wrk-xxx"
python weread_bulk_downloader.py --book-id <bookId> --out-dir ./weread_cache --limit 200
# 不传 --limit 则全量; 重跑同一命令 = 断点续传 + 补注划线
```

- 输出：`weread_cache/<bookId>/<bookId>.epub`（内部 `.parts/` 是断点缓存，勿删）
- **限流保护**：默认每章 0.4s 间隔 + 失败重试 6 次。千章级大书约 1~1.5 小时，属正常（防微信读书风控）。报 `errcode -10102` 说明太快，增大 `--sleep`（0.5~0.6）重跑（断点续传不重下）。
- **HTTP 499**：单次请求超时，脚本自动跳过该章，跑完检查日志 `weread_cache/download_<bookId>.log` 的 `!! 跳过` / `失败`，对失败章节单独重试（可写一次性补跑脚本，3 次重试 + 3s 间隔）。

## 3.1 补封面（下载的 EPUB 默认无封面）

```bash
python add_cover.py --book-id <bookId> --out-dir ./weread_cache
```

- 自动从 `/book/info` 取封面 URL 下载并写入 EPUB（`meta name=cover` + `properties="cover-image"` item + `OEBPS/images/cover.jpg`）；原文件备份为 `.epub.bak`。
- 用 API Key 走 gateway，无需 Cookie。

## 4. 生成想法库（推荐，点击划线弹想法必需）

KOReader 插件点击划线弹想法是从书目录下 `thoughts.db`（SQLite，表 `review_items`）读取，不是 EPUB 内嵌。

```bash
python build_thought_db.py --book-id <bookId> --out-dir ./weread_cache
```

- 输出：`weread_cache/<bookId>/thoughts.db`；200 章约 10 分钟；499 超时章节脚本会跳过，跑完按日志补跑（思路同第 3 步）。

## 5. 导入 KOReader（设备端）

1. **（推荐）先在 KOReader 插件里手动下载 1 章**该书 → 生成 `catalog.json` 等目录元数据，识别最稳
2. 把 `weread_cache/<bookId>/` **整个目录**（含 `.epub` 与 `thoughts.db`）拷到设备：
   - 安卓：`/storage/emulated/0/koreader/weread/cache/<bookId>/`
   - Kindle：`koreader/weread/cache/<bookId>/`；Kobo：`.kobo/koreader/weread/cache/<bookId>/`
   - **目录名必须 = bookId**
3. KOReader → 工具 → 微信读书 → 设置 → 缓存管理 → **扫描并关联本地书籍**
4. 设置 → 进度管理：打开「打开时拉取进度」「关闭时上传进度」；启用「阅读时间上报」

> 插件通过"目录名 = 书架 bookId + 目录内有 .epub"识别（`scan.lua`），识别后即为缓存书籍，进度/时长同步生效。

## 6. 验证清单（交付前必查）

- [ ] `weread_cache/<bookId>/<bookId>.epub` 存在，用 zipfile 检查 `OEBPS/text/` 章节数 = 预期
- [ ] EPUB 含封面：`content.opf` 有 `name="cover"` 与 `properties="cover-image"`，且有 `OEBPS/images/cover.*`
- [ ] 章节 HTML 含 `wr-underline`（划线已注入）且 CSS 含 `.wr-underline` 样式
- [ ] `thoughts.db` 存在，`SELECT COUNT(*) FROM review_items` > 0
- [ ] 告知用户设备上的最终路径和 KOReader 操作步骤

## 7. 常见故障速查

| 症状 | 原因 | 处理 |
|------|------|------|
| 划线位置整体偏移 | txt 格式书被重包装后注入 | 确保用本工具下载（txt 段落模型注入），勿自行改 HTML |
| 划线能看但点不动 | 缺 thoughts.db | 执行第 4 步 |
| 目录为空/识别不了 | 缺 catalog.json | KOReader 插件里先下载 1 章 |
| 下载报 -10102 | 限流 | `--sleep 0.5~0.6` 重跑（断点续传） |
| 下载报 499 | 请求超时 | 看日志找失败章节，重试脚本补跑 |
| 进度不同步 | 同步开关未开 | 第 5 步第 4 条 |
| 代理报错 | 走了代理 | unset 代理环境变量后重跑 |

## 8. 安全与合规

- 仅供个人学习/备份自购书籍，遵守微信读书用户协议；账号风控风险自负
- 凭证不出现在日志/提交中；建议用后提示用户轮换
