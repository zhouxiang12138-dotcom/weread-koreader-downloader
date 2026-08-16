# weread-koreader-downloader

在 **电脑端** 批量下载微信读书书籍（含划线/想法注入），生成 **KOReader 可直接识别** 的缓存 EPUB，实现：

- ✅ 电脑宽带全量下载，解决 KOReader 设备端下载慢/易中断的问题
- ✅ **阅读进度双向同步**（打开拉取云端进度 / 关闭上传本地进度）
- ✅ **阅读时长上报**（微信读书统计/排行榜可见）
- ✅ **划线与想法注入**（橙色虚线下划线，与 KOReader 插件原生效果一致）

> ⚠️ **免责声明**：本项目仅供个人学习与技术研究，用于备份自己已购/已读的书籍。基于微信读书网页端逆向接口，存在账号风控风险，请遵守微信读书用户协议，后果自负。严禁用于传播盗版内容。

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
- 微信读书是国内服务，**不能走海外代理**（会被拒/超时），务必直连。
- 下载过快会触发限流（`errcode -10102`），需降速 + 失败重试。

## 快速开始

### 0. 环境

- Python 3.9+
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
# 下载前 200 章（含划线注入）
python weread_bulk_downloader.py \
  --book-id 29664125 \
  --cookie-string "wr_vid=xxx; wr_gid=yyy" \
  --api-key "wrk-xxx" \
  --limit 200 \
  --out-dir ./weread_cache

# 全量下载（不传 --limit 即全部；重跑同一命令 = 断点续传/补注划线）
python weread_bulk_downloader.py \
  --book-id 29664125 --cookie-string "..." --api-key "wrk-xxx" --out-dir ./weread_cache
```

`--book-id` 可从书架 API 获取，或参考 `weread.koplugin` 的说明。输出：`weread_cache/<bookId>/<bookId>.epub`

### 3. 导入 KOReader 并解锁同步

1. 把 `weread_cache/<bookId>/` 整个目录拷到设备（如安卓：`/storage/emulated/0/koreader/weread/cache/<bookId>/`）
2. KOReader → 工具 → 微信读书 → 设置 → 缓存管理 → 点 **扫描并关联本地书籍**
3. 设置 → 进度管理：打开「打开时拉取进度」「关闭时上传进度」
4. 设置 → 阅读时间上报：启用（阅读时长回传微信读书统计）

> 关键点：KOReader 插件通过"目录名 = 书架 bookId + 目录内有 .epub"识别缓存书籍（`scan.lua`），识别后即为「微信读书缓存书籍」，进度/时长同步全部生效。

## 工具列表

| 文件 | 作用 |
|------|------|
| `weread_bulk_downloader.py` | 主下载器：限流重试 + 断点续传 + 划线/想法注入（txt/epub 双格式） |
| `extract_weread_auth.py` | 从 KOReader 的 `weread.lua` 提取 Cookie 与 API Key |
| `fetch_weread_epub.py` | 协议层（来自 weread.koplugin，AGPL-3.0），请保持同目录 |

## 常见问题

- **下载报 `WinError 10061` / 超时**：系统代理挡了国内直连。执行前 `unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy`，或把 `weread.qq.com`、`i.weread.qq.com` 加入代理直连规则。
- **`errcode -10102` 限流**：增大 `--sleep`（0.4~0.6）和 `--retry-delay`（30s+），工具会自动重试。
- **划线位置偏移**：txt 格式书籍必须用本工具的段落模型注入（不要对重包装后的 HTML 注入）。
- **想法弹窗不显示**：KOReader 插件点击划线弹想法依赖其 `thoughts.db`（SQLite），本工具暂未生成，划线高亮不受影响。

## 致谢与许可

协议层与注入逻辑移植自 [finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)（AGPL-3.0）。本项目作为其衍生作品，同样以 **AGPL-3.0** 发布（见 `LICENSE`）。请遵守微信读书用户协议。
