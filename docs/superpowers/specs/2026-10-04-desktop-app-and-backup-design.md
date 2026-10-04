# Mira 桌面 App 与自动备份 设计文档

- 日期：2026-10-04
- 状态：待用户审阅
- 范围：一键启动（独立窗口 App）+ 数据库自动备份

## 1. 目标

| # | 需求 | 来源 |
|---|---|---|
| D1 | 不用打开终端、不用记命令，点一下图标就能和 Mira 聊天 | 用户 |
| D2 | 像普通 App 一样：有自己的窗口，关掉窗口 Mira 就停止 | 用户选择方案 B |
| D3 | 聊天和记忆自动备份，不用手动复制数据库 | 用户 |
| D4 | 备份放在项目内 `data/backups/`，以后可在 `.env` 改位置 | 用户选择 |

**不做**（YAGNI）：能发给别人的完整打包 App（要带 Python 和模型、要苹果签名）；恢复备份的按钮；云端备份；开机自启。

**成功标准**：在"应用程序"里点 Mira 图标 → 出现 Mira 窗口并能正常聊天、看记忆、播放视频背景 → 关窗口后 8000 端口释放；运行一天后 `data/backups/` 里有一份可以打开、内容完整的备份。

## 2. 桌面 App

### 2.1 使用方式

- 运行一次 `uv run python -m mira.make_app`，生成 `~/Applications/Mira.app`，图标取自 `theme/mira/avatar.png`（没有就用默认图标）。可以拖进 Dock。
- 点开后立刻出现窗口，显示"Mira 正在醒来…"；服务就绪后窗口自动跳到聊天页。首次启动要下载约 90MB 的向量模型，等待会更久。
- 窗口内通过现有的"记忆"链接进入记忆页。
- 关窗口或 ⌘Q：Mira 正常停止（数据库关闭、后台任务取消）。未整理的消息下次启动会补整理（现有逻辑）。
- 已经有 Mira 在运行（例如在终端启动的）：App 只打开窗口连上它，关窗口时不停止它。
- 原有的 `uv run python -m mira` 终端启动方式不变。

### 2.2 结构

**`mira/desktop.py`**（新）—— App 的入口，单进程：

1. 读取配置（`load_settings`）。配置错误时，在窗口里显示中文错误。
2. 检查端口：
   - 端口已被占用且 `GET /api/memory-status` 正常返回 → 判断为已有 Mira 在运行，只开窗口，不启动服务、关窗时不停止。
   - 端口被其他程序占用 → 窗口显示错误。
   - 端口空闲 → 在后台线程启动 uvicorn（`uvicorn.Server`，以便之后设置 `should_exit` 让它正常退出）。
3. 主线程创建 pywebview 窗口（macOS 要求窗口在主线程），先显示内置的"正在醒来"页面；后台线程轮询服务就绪后让窗口加载 `http://127.0.0.1:<port>/`。
4. 服务启动失败（模型下载失败 `EmbedderLoadError` 等）→ 窗口显示中文错误和日志位置。
5. 窗口关闭 → 如果服务是本进程启动的，设置 `should_exit` 并等待线程结束（设上限，超时直接退出）。

**`mira/make_app.py`**（新）—— 生成 `.app`：

- 目录结构：`Mira.app/Contents/{Info.plist, MacOS/Mira, Resources/Mira.icns}`。
- `MacOS/Mira` 是一个 shell 脚本：`cd` 到项目目录，`exec` uv 运行 `python -m mira.desktop`。项目目录和 uv 的绝对路径在生成时写入（Finder 启动的 App 没有 shell 的 PATH）。
- 图标：用 macOS 自带的 `sips` 生成各尺寸，再用 `iconutil` 打包成 `.icns`。没有 `avatar.png` 或转换失败时，不放图标，App 用系统默认图标。
- 已存在同名 App 时直接覆盖（它只是生成物，没有用户数据）。
- 项目文件夹移动后 App 失效，重新运行 `make_app` 即可；README 写明。

**日志**：App 模式下没有终端，日志写到 `data/logs/mira.log`（文件按大小轮换，最多保留几份）。终端模式仍然输出到终端。

**依赖**：新增 `pywebview`（macOS 上使用系统 WebKit，不附带浏览器）。

### 2.3 待实测的风险

| 风险 | 处理 |
|---|---|
| Dock 显示 "Python" 的名字和图标，而不是 Mira | 运行时用 pyobjc 设置应用图标和名字；实测确认效果，做不到就在 README 说明 |
| 视频背景在 WKWebView 里不自动播放 | 视频已是静音循环，预计可以；实测确认 |
| WebSocket / Origin 校验 | 窗口加载的就是 `http://127.0.0.1:<port>`，Origin 与浏览器一致，预计无影响；实测确认 |

## 3. 自动备份

### 3.1 使用方式

- Mira 运行期间每天备份一次；启动时距上次备份超过 24 小时会立即补一次。
- 备份存到 `data/backups/`，文件名 `mira-YYYYMMDD-HHMMSS.db`，只保留最近 14 份。
- 记忆页状态区新增"上次备份：…"和「立即备份」按钮；备份失败时显示简短的错误提示。
- 恢复：README 写明步骤——退出 Mira → 用备份文件覆盖 `data/mira.db` → 删除 `data/mira.db-wal` 和 `data/mira.db-shm`。不做恢复按钮。

### 3.2 结构

**`mira/backup.py`**（新）：

- `backup_database(db_path, backup_dir, now) -> Path`
  - 新开一个只读连接，用 `sqlite3.Connection.backup()` 写到 `backup_dir` 下的临时文件（运行中、WAL 模式下也能得到一致快照）。
  - 对临时文件执行 `PRAGMA quick_check`，结果不是 `ok` 就删除临时文件并报错。
  - 通过后改名为正式文件名。正式文件名前缀取数据库文件名（`mira.db` → `mira-…`，`dev.db` → `dev-…`）。
- `prune_backups(backup_dir, prefix, keep)`：只匹配 `<prefix>-YYYYMMDD-HHMMSS.db` 格式的文件，按文件名排序保留最新 `keep` 份；不匹配的文件一律不动。
- 同一秒内重复备份（例如连点按钮）时文件名已存在 → 不重写，直接返回已有的那份；绝不覆盖已有备份。

**调度器**（`mira/scheduler.py`）：

- 新增 `backup` 任务，复用 `job_state` 记录上次成功时间、失败退避 30 分钟、错误信息进入 `status()`。
- `startup()` 和 `tick()`：上次成功为空或距今超过 24 小时就执行。
- 备份不走模型任务的那把锁（它不调用模型，不该被长时间的整理任务挡住），用 `asyncio.to_thread` 执行，不阻塞聊天。
- `status()` 增加 `last_backup_at`、`backup_error` 字段。
- 新增 `backup_now()` 供接口调用：立即执行一次，忽略 24 小时间隔，但同一时刻只允许一个备份在跑。

**接口**：`POST /api/backup` → 成功返回 `{"path": 文件名, "at": 时间}`，失败返回 500 和中文错误。受现有的跨站写请求拦截保护。

**配置**：`BACKUP_DIR`（默认 `data/backups`，相对项目根目录解析）、`BACKUP_KEEP`（默认 14，必须 ≥ 1）。写进 `.env.example`。

**前端**（`web/memory.html`、`web/memory.js`）：状态区新增一行和一个按钮；按钮点击后禁用直到返回；全部用 `textContent`。

## 4. 测试

**单元测试（不调用 API）**

- 备份：内容与原库一致；数据库处于 WAL 模式且有未合并写入时备份也完整；临时文件在失败时被清理。
- 清理：超过 `keep` 份删最旧；不匹配命名格式的文件不删；`dev-` 和 `mira-` 前缀互不影响。
- 调度：24 小时内不重复；超过 24 小时触发；失败后 30 分钟内不重试；备份不等待模型任务的锁；`status()` 含新字段。
- 接口：`POST /api/backup` 成功和失败；跨站请求被拒。
- 配置：`BACKUP_KEEP` 非法值报错；路径相对项目根目录。
- `make_app`：生成的目录结构、`Info.plist` 字段、启动脚本中的路径正确（在临时目录生成，不碰 `~/Applications`）。
- `desktop`：端口判断逻辑（已有 Mira / 其他程序 / 空闲）可以抽成纯函数测试；窗口部分不做单元测试。

**实测（开发模式，`data/dev.db`，绝不连真实库）**

- 启动前确认 `db_path` 解析为 `dev.db`。
- App 启动 → 等待页 → 聊天页；发假消息、看记忆页、视频背景播放；点「立即备份」后 `data/backups/` 出现 `dev-….db`。
- 关窗口后端口释放；终端已运行 Mira 时 App 只开窗、关窗不影响终端。
- Dock 图标和名字的实际效果如实记录。
