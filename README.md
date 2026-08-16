# weread-koreader-downloader

**专为 KOReader 微信读书插件（[weread.koplugin](https://github.com/finlater/weread.koplugin)）设计**的电脑端批量下载器：在 **电脑端** 下载微信读书书籍（含划线/想法注入），产出插件可直接识别的「微信读书缓存书籍」目录，实现：

- ✅ 电脑宽带全量下载，解决 KOReader 设备端下载慢/易中断的问题
- ✅ **阅读进度双向同步**（打开拉取云端进度 / 关闭上传本地进度）
- ✅ **阅读时长上报**（微信读书统计/排行榜可见）
- ✅ **划线与想法注入**（橙色虚线下划线，与插件原生效果一致）

> ⚠️ **免责声明**：本项目仅供个人学习与技术研究，用于备份自己已购/已读的书籍。基于微信读书网页端逆向接口，存在账号风控风险，请遵守微信读书用户协议，后果自负。严禁用于传播盗版内容。

## 安装位置（配套环境）

本工具**必须配合 KOReader 的 WeRead 插件（weread.koplugin）使用**——下载的书只有放进插件的缓存目录，才会被识别为「微信读书缓存书籍」，从而解锁进度/时长同步。请先确保插件已装：

**① 插件本体**（KOReader 设备上）
- 来源：下载 [weread.koplugin Releases](https://github.com/finlater/weread.koplugin/releases) 的 `weread.koplugin-vX.Y.Z.zip`
- 安装到：`koreader/plugins/weread.koplugin/`（解压后整个文件夹）
- 重启 KOReader，菜单出现：工具 → 微信读书（首次需扫码登录）

**② 本工具下载的书**（拷入设备后的位置）
- 放到：`koreader/weread/cache/<bookId>/`（**目录名必须等于书架 bookId**，目录内放 .epub）

**③ 登录凭证**（`koreader/settings/weread.lua`）——插件登录后自动生成，可被 `extract_weread_auth.py` 读取，用于电脑端下载

常见设备路径：

| 设备 | KOReader 根目录 |
|------|----------------|
| 安卓（手机/平板） | `/storage/emulated/0/koreader/` |
| Kindle | `koreader/`（USB 根目录） |
| Kobo | `.kobo/koreader/` |

目录结构示例（安卓）：

```
/storage/emulated/0/koreader/
├── plugins/weread.koplugin/     ← 插件本体（①）
├── settings/weread.lua          ← 登录凭证（③，可提取）
└── weread/cache/29664125/       ← 本工具下载的书（②，目录名=bookId）
    ├── 29664125.epub            ← 整书
    └── thoughts.db              ← 想法库（build_thought_db.py 生成，可选）
```

## 原理

| 能力 | 使用接口 | 认证 |
|------|---------|------|
| 章节目录/阅读进度 | 官方 Skill API（Agent Gateway） | API Key |
| 章节正文（加密分片解码） | `weread.qq.com/web/book/chapter/t_0|t_1|e_0|e_1|e_3` | Cookie |
| 划线与想法注入 | Gateway `/book/underlines` `/book/readreviews` | API Key |
| 进度/时长上报 | `weread.qq.com/web/book/read`（KOReader 插件侧） | Cookie |

协议层复用自 [finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)（AGPL-3.0），本书中的 Python 下载器是其协议（`fetch_weread_epub.py`）的封装增强：**限流重试 + 断点续传 + 划线注入**。

**关键经验（已踩过的坑）**：
- 微信读书正文有两种格式：`epub`（原始 HTML）与 `txt`（纯文本，段落 = `\r\n` + 两个全角空格 `\u3000\u3000` 缩进）。**划线 range 直接基于这份原始文本**，重新包装 HTML 会导致整体偏移——txt 章节必须按段落逐段注入。
- 划线注入必须在图片地址重写**之前**（range 基于原始 HTML）。
- 下载过快会触发限流（`errcode -10102`），需降速 + 失败重试。

## 快速开始

### 0. 环境

- Python 3.10+
- 与 [weread.koplugin](https://github.com/finlater/weread.koplugin) 同级目录（或把其 `scripts/fetch_weread_epub.py` 放到本目录）

```bash
git clone https://github.com/finlater/weread.koplugin
git clone https://github.com/<your>/weread-koreader-downloader
cd weread-koreader-downloader
```

### 1. 获取凭证

**Cookie**（下载正文用，两种方式任选）：
- 方式 A：浏览器登录 `weread.qq.com` → F12 → Application → Cookies → 找 `wr_vid` 和 `wr_gid`（或 `wr_skey`）
- 方式 B：如果你已在 KOReader 上登录过微信读书，把设备上的 `koreader/settings/weread.lua` 拷到电脑，用附带的 `extract_weread_auth.py` 自动提取：

```bash
python extract_weread_auth.py /path/to/weread.lua
# 输出 Cookie 头字符串 与 API Key
```

**API Key**（划线/想法用）：微信读书 App → 我 → 设置 → 微信读书 Skill → 获取 API Key（形如 `wrk-xxxx`）

### 2. 下载

```bash
# 方式一: 命令行传凭证
python weread_bulk_downloader.py \
  --book-id 29664125 \
  --cookie-string "wr_vid=xxx; wr_gid=yyy" \
  --api-key "wrk-xxx" \
  --limit 200 \
  --out-dir ./weread_cache

# 方式二: 环境变量(避免凭证出现在进程列表/历史记录)
export WEREAD_COOKIE="wr_vid=xxx; wr_gid=yyy"
export WEREAD_API_KEY="wrk-xxx"
python weread_bulk_downloader.py --book-id 29664125 --limit 200 --out-dir ./weread_cache

# 全量下载（不传 --limit 即全部；重跑同一命令 = 断点续传/补注划线）
python weread_bulk_downloader.py \
  --book-id 29664125 --cookie-string "..." --api-key "wrk-xxx" --out-dir ./weread_cache
```

`--book-id` 可从书架 API 获取，或参考 `weread.koplugin` 的说明。输出：`weread_cache/<bookId>/<bookId>.epub`

### 2.5 生成想法库（点击划线弹「想法」必需）

KOReader 插件点击划线弹出想法（他人评论/笔记），是从书目录下的 `thoughts.db`（SQLite）实时读取的，**不是从 EPUB 内嵌**。下载完书后用一条命令生成：

```bash
python build_thought_db.py --book-id 29664125 --api-key "wrk-xxx" --out-dir ./weread_cache
# 生成 weread_cache/29664125/thoughts.db（每章几百条想法，200 章约 10 分钟）
```

> 只想要划线高亮、不需要想法弹窗的话，这步可跳过。

### 3. 导入 KOReader 并解锁同步

> 前置：插件已装、设备可 USB 连接（安装位置见上方「安装位置」章节）

1. **（推荐）先在 KOReader 插件里手动下载 1 章**：打开 微信读书 → 书架 → 找到这本书 → 下载任意一章。插件会在缓存目录生成**目录元数据**（`catalog.json`、`metadata.json` 等），整书识别最稳
2. 把 `weread_cache/<bookId>/` 整个目录拷到设备（安卓示例：`/storage/emulated/0/koreader/weread/cache/<bookId>/`，**目录名必须保持 bookId**，与第 1 步生成的目录内容合并）
3. KOReader → 工具 → 微信读书 → 设置 → 缓存管理 → 点 **扫描并关联本地书籍**
4. 设置 → 进度管理：打开「打开时拉取进度」「关闭时上传进度」
5. 设置 → 阅读时间上报：启用（阅读时长回传微信读书统计）

> 关键点：插件通过"目录名 = 书架 bookId + 目录内有 .epub"识别缓存书籍（`scan.lua`），识别后即为「微信读书缓存书籍」，进度/时长同步全部生效。先在插件里下载一章可先生成目录元数据，之后用电脑下载的整书覆盖/补充即可。

## 工具列表

| 文件 | 作用 |
|------|------|
| `weread_bulk_downloader.py` | 主下载器：限流重试 + 断点续传 + 划线/想法注入（txt/epub 双格式） |
| `build_thought_db.py` | 生成 `thoughts.db` 想法库（点击划线弹想法必需，按插件 schema 写入） |
| `extract_weread_auth.py` | 从 KOReader 的 `weread.lua` 提取 Cookie 与 API Key |
| `fetch_weread_epub.py` | 协议层（来自 weread.koplugin，AGPL-3.0），请保持同目录 |

## 常见问题

- **`errcode -10102` 限流**：增大 `--sleep`（0.4~0.6）和 `--retry-delay`（30s+），工具会自动重试。
- **划线位置偏移**：txt 格式书籍必须用本工具的段落模型注入（不要对重包装后的 HTML 注入）。
- **想法弹窗不显示**：KOReader 插件点击划线弹想法依赖其 `thoughts.db`（SQLite），本工具暂未生成，划线高亮不受影响。
- **凭证安全**：优先使用环境变量 `WEREAD_COOKIE` / `WEREAD_API_KEY`，避免在命令行明文传参。

## 致谢与许可

协议层与注入逻辑移植自 [finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)（AGPL-3.0）。本项目作为其衍生作品，同样以 **AGPL-3.0** 发布（见 `LICENSE`）。请遵守微信读书用户协议。
