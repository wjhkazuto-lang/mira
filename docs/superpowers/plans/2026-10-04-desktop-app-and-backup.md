# 桌面 App 与自动备份 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户点 Dock 图标就能在独立窗口里和 Mira 聊天，并且数据库每天自动备份。

**Architecture:** 备份是一个纯函数模块 `mira/backup.py`，由现有 `Scheduler` 当作第三个任务调度（独立的锁，放进线程执行），并通过 `POST /api/backup` 暴露给记忆页按钮。桌面 App 是 `mira/desktop.py`：后台线程跑 uvicorn，主线程跑 pywebview 窗口；`mira/make_app.py` 生成一个调用它的 `.app` 外壳。

**Tech Stack:** Python 3.12、FastAPI、uvicorn 0.54、sqlite3 在线备份 API、pywebview（macOS WebKit）、pyobjc（pywebview 自带）、macOS `sips`/`iconutil`、pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-10-04-desktop-app-and-backup-design.md`

## Global Constraints

- 开发和测试**绝不碰** `data/mira.db` 和项目里真实的 `data/backups/`；任何会启动 `create_app` 的测试都要把 `DB_PATH`、`BACKUP_DIR` 指到 `tmp_path`。
- 提交只用 `git add <具体路径>`，不用 `-A` / `.`；提交前看 `git status --short`，出现 `data/`、`theme/`、`*.local.md`、媒体文件就停下。
- 提交信息**不加** `Co-Authored-By` 行；作者用仓库本地 git 配置。
- 面向用户的文字一律中文；前端只用 `textContent`。
- 备份默认值：`BACKUP_DIR=data/backups`、`BACKUP_KEEP=14`、间隔 24 小时、失败退避 30 分钟（复用 `FAILURE_BACKOFF`）。
- 备份文件名：`<数据库文件名去掉扩展名>-YYYYMMDD-HHMMSS.db`（`mira.db` → `mira-20261004-153000.db`）。
- App 生成位置默认 `~/Applications/Mira.app`；Bundle 标识 `local.mira.desktop`（不含任何个人信息）。
- 新依赖只加 `pywebview`。
- 测试命令：`cd /Users/megu/Projects/Mira && ~/.local/bin/uv run pytest -q`

## Review Focus

1. **测试把备份写进真实的 `data/backups/`**：`tests/test_app.py` 里的 `create_app` 启动时会触发启动备份；必须把每个 fixture 都改成 `BACKUP_DIR=tmp_path/...`，并加一条测试断言整个测试套件跑完后 `PROJECT_ROOT/data/backups` 下没有新增 `t-*`、`lifecycle-*` 文件（Task 2）。
2. **备份目录不存在 / 不可写 / 磁盘满**：用户期望 Mira 照常聊天、状态区显示"备份失败"、不留下半截文件（Task 1、Task 2 的测试）。
3. **`make_app` 覆盖了不是 Mira 的 App**：目标位置已有别的 `Mira.app`（Bundle 标识不是 `local.mira.desktop`）时必须拒绝，而不是删掉（Task 5）。
4. **项目路径含空格或中文**（例如 `~/我的项目/Mira`）：启动脚本的引号、SQLite 的 URI 都要正确（Task 1、Task 5）。
5. **8000 端口已被占用**：终端里的 Mira 在跑 → 只开窗、关窗不影响它；被别的程序占用 → 窗口显示中文错误（Task 4）。

---

### Task 1: 备份模块与配置

**Files:**
- Create: `mira/backup.py`
- Modify: `mira/config.py`（`Settings` 加两个字段、路径解析、校验）
- Modify: `.env.example`（加两项及中文注释）
- Test: `tests/test_backup.py`、`tests/test_config.py`

**Interfaces:**
- Produces:
  - `class BackupError(Exception)`：`user_message: str` 属性（中文，可直接给页面）。
  - `backup_database(db_path: Path, backup_dir: Path, now: datetime) -> Path`
  - `prune_backups(backup_dir: Path, prefix: str, keep: int) -> list[Path]`（返回被删除的文件）
  - `run_backup(db_path: Path, backup_dir: Path, keep: int, now: datetime) -> Path`（备份后清理，返回本次备份）
  - `Settings.backup_dir: Path`（默认 `Path("data/backups")`，相对 `PROJECT_ROOT` 解析）、`Settings.backup_keep: int`（默认 `14`）

- [ ] **Step 1: 写失败的测试** `tests/test_backup.py`

```python
def make_db(path):  # WAL 模式，留一条未 checkpoint 的写入
    db = sqlite3.connect(path); db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t(x)"); db.execute("INSERT INTO t VALUES ('你好')"); db.commit()
    return db  # 保持打开，-wal 文件里还有数据

NOW = datetime.fromisoformat("2026-10-04T15:30:00+08:00")

def test_backup_is_complete_copy_while_db_open(tmp_path):
    src = make_db(tmp_path / "mira.db")
    out = backup_database(tmp_path / "mira.db", tmp_path / "backups", NOW)
    assert out == tmp_path / "backups" / "mira-20261004-153000.db"
    assert sqlite3.connect(out).execute("SELECT x FROM t").fetchall() == [("你好",)]
    assert [p.name for p in (tmp_path / "backups").iterdir()] == [out.name]  # 没有残留临时文件

def test_creates_missing_backup_dir(tmp_path): ...  # backup_dir 多层不存在 → 自动创建

def test_path_with_spaces_and_chinese(tmp_path):
    d = tmp_path / "我的 项目"; d.mkdir(); make_db(d / "mira.db")
    assert backup_database(d / "mira.db", d / "备份", NOW).exists()

def test_same_second_returns_existing_without_overwrite(tmp_path):
    make_db(tmp_path / "mira.db")
    first = backup_database(tmp_path / "mira.db", tmp_path / "b", NOW)
    mtime = first.stat().st_mtime_ns
    assert backup_database(tmp_path / "mira.db", tmp_path / "b", NOW) == first
    assert first.stat().st_mtime_ns == mtime

def test_missing_source_raises_backup_error(tmp_path):
    with pytest.raises(BackupError): backup_database(tmp_path / "nope.db", tmp_path / "b", NOW)
    assert not (tmp_path / "nope.db").exists()  # 不能因为只读连接而"创建"出空库

def test_unwritable_dir_raises_and_leaves_nothing(tmp_path):
    make_db(tmp_path / "mira.db"); ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o500)
    with pytest.raises(BackupError): backup_database(tmp_path / "mira.db", ro, NOW)
    ro.chmod(0o700); assert list(ro.iterdir()) == []

def test_prune_keeps_newest_and_ignores_other_files(tmp_path):
    for i in range(1, 17): (tmp_path / f"mira-202610{i:02d}-120000.db").touch()
    for name in ("dev-20261001-120000.db", "mira-手动.db", "notes.txt"): (tmp_path / name).touch()
    removed = prune_backups(tmp_path, "mira", 14)
    assert sorted(p.name for p in removed) == ["mira-20261001-120000.db", "mira-20261002-120000.db"]
    left = {p.name for p in tmp_path.iterdir()}
    assert {"dev-20261001-120000.db", "mira-手动.db", "notes.txt"} <= left and len(left) == 17

def test_run_backup_prunes_with_db_stem_prefix(tmp_path): ...  # dev.db 的备份只清理 dev-*，不动 mira-*
```

`tests/test_config.py` 增加：

```python
def test_backup_defaults_and_paths():
    s = load_settings({"DEEPSEEK_API_KEY": "k"})
    assert s.backup_dir == PROJECT_ROOT / "data" / "backups" and s.backup_keep == 14
    assert load_settings({"DEEPSEEK_API_KEY": "k", "BACKUP_DIR": "x/b"}).backup_dir == PROJECT_ROOT / "x" / "b"

@pytest.mark.parametrize("v", ["0", "-1", "abc"])
def test_backup_keep_invalid(v):
    with pytest.raises(ConfigError): load_settings({"DEEPSEEK_API_KEY": "k", "BACKUP_KEEP": v})
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `~/.local/bin/uv run pytest tests/test_backup.py tests/test_config.py -q`
Expected: FAIL（`ModuleNotFoundError: mira.backup`、`Settings` 没有 `backup_dir`）

- [ ] **Step 3: 实现 `mira/backup.py` 和配置**

- 源连接：`sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)`。`mode=ro` 保证源文件不存在时报错而不是新建空库；`as_uri()` 负责处理空格和中文的转义。
- 先写到同目录的 `.<正式名>.tmp`，`src.backup(dst)` 完成后执行 `PRAGMA quick_check`，结果为 `ok` 就 `os.replace` 成正式名。任何异常都删掉临时文件，再抛 `BackupError`。
- `BackupError.user_message`：`OSError` → `f"备份失败：{e.strerror or e}"`；`sqlite3.Error` → `"备份失败：数据库无法读取"`；完整性检查失败 → `"备份失败：备份文件校验未通过"`。
- 清理用的正则：`rf"^{re.escape(prefix)}-\d{{8}}-\d{{6}}\.db$"`，按文件名倒序排列，保留前 `keep` 份。
- `config.py`：在路径解析的元组里加上 `"backup_dir"`；`backup_keep < 1` 时抛 `ConfigError("BACKUP_KEEP 必须是大于等于 1 的整数：…")`。
- `.env.example` 在 `DB_PATH` 后面加：

```
# 自动备份放在哪里（每天一份）
BACKUP_DIR=data/backups
# 最多保留几份备份，更早的自动删除
BACKUP_KEEP=14
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `~/.local/bin/uv run pytest tests/test_backup.py tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mira/backup.py mira/config.py .env.example tests/test_backup.py tests/test_config.py
git commit -m "feat: consistent sqlite backups with retention"
```

---

### Task 2: 调度备份 + 接口

**Files:**
- Modify: `mira/scheduler.py`
- Modify: `mira/main.py`（构造 `Scheduler` 时传入备份函数；新增 `POST /api/backup`）
- Modify: `tests/test_app.py`（所有 `load_settings` 加 `BACKUP_DIR`）
- Test: `tests/test_scheduler.py`、`tests/test_app.py`

**Interfaces:**
- Consumes: `run_backup`、`BackupError`（Task 1）
- Produces:
  - `Scheduler(..., backup: Callable[[], Path] | None = None)`：同步函数，调度器负责放进线程执行。为 `None` 时备份功能关闭（现有测试不受影响）。
  - `Scheduler.backup_now() -> Path`（async）：忽略 24 小时间隔和失败退避，立即执行；失败时记录错误并抛 `BackupError`。
  - `Scheduler.status()` 新增字段：`last_backup_at: str | None`（ISO）、`backup_running: bool`、`backup_error: str | None`
  - `POST /api/backup` → 200 `{"file": "<文件名>", "at": "<ISO>"}`；失败时返回 500 `{"detail": user_message}`

- [ ] **Step 1: 写失败的测试**

`tests/test_scheduler.py`（复用文件里的 `Job`、`make`、`at`、`clock`，给 `make` 加一个 `backup=None` 参数）：

```python
class Backup:
    def __init__(self, fail=False): self.runs = 0; self.fail = fail
    def __call__(self):
        self.runs += 1
        if self.fail: raise BackupError("备份失败：磁盘已满")
        return Path(f"/tmp/mira-{self.runs}.db")

async def test_startup_backs_up_when_never_done(store, clock): ...      # runs == 1，get_job_last_run("backup") 已设置
async def test_backup_not_repeated_within_24h(store, clock): ...        # startup 后 advance 23h → tick → runs 仍为 1
async def test_backup_after_24h(store, clock): ...                       # advance 24h → tick → runs == 2
async def test_backup_failure_backs_off_30min_and_reports(store, clock):
    # fail=True：startup → status()["backup_error"] == "备份失败：磁盘已满"；advance 29min tick 不重试；advance 2min tick 重试
async def test_backup_does_not_wait_for_model_lock(store, clock):
    # writer.run 挂在 asyncio.Event 上不返回；同时 await s.backup_now() 在 1 秒内完成（asyncio.wait_for）
async def test_backup_now_ignores_interval_and_raises_on_failure(store, clock): ...
async def test_status_has_backup_fields(store, clock):
    assert {"last_backup_at", "backup_running", "backup_error"} <= set(s.status())
async def test_no_backup_callable_means_disabled(store, clock): ...      # backup=None：startup/tick 不报错，status 字段为空
```

`tests/test_app.py`：
- 所有 `load_settings({...})` 都加上 `"BACKUP_DIR": str(tmp_path / "backups")`。
- 新增：

```python
def test_backup_endpoint(client, tmp_path):
    r = client.post("/api/backup")
    assert r.status_code == 200 and r.json()["file"].startswith("t-")
    assert (tmp_path / "backups" / r.json()["file"]).exists()
    assert client.get("/api/memory-status").json()["last_backup_at"]

def test_backup_endpoint_rejects_cross_site(client):
    assert client.post("/api/backup", headers={"Origin": "http://evil.example"}).status_code == 403

def test_backup_endpoint_failure_is_500_with_chinese(app, tmp_path): ...  # BACKUP_DIR 指向一个已存在的普通文件 → 500，detail 以"备份失败"开头

def test_app_tests_never_write_real_backups():
    real = PROJECT_ROOT / "data" / "backups"
    assert not real.exists() or not [p for p in real.iterdir() if not p.name.startswith(("mira-", "dev-"))]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `~/.local/bin/uv run pytest tests/test_scheduler.py tests/test_app.py -q`
Expected: FAIL（`Scheduler` 不接受 `backup`；`/api/backup` 返回 404/405）

- [ ] **Step 3: 实现**

- `scheduler.py`：加常量 `BACKUP_INTERVAL = timedelta(hours=24)` 和一把独立的 `self._backup_lock = asyncio.Lock()`。
- `startup()` **先**备份，再跑写入器和反思器：在模型改动记忆之前先留一份快照。`tick()` 在写入器、反思器之后检查备份是否到期。
- 记录上次运行时间、`_not_before`、`_errors` 的逻辑和 `_run` 一样，名字用 `"backup"`。可以把 `_run` 改成接受 `lock` 和异步可调用对象，避免写两遍。
- 备份用 `await asyncio.to_thread(self._backup)` 执行。错误信息：`BackupError` → `e.user_message`，其他异常 → `"备份失败，请查看运行日志"`。
- `main.py`：`backup=functools.partial(...)`，调用时取当前时间，形如 `lambda: run_backup(settings.db_path, settings.backup_dir, settings.backup_keep, clock.now())`。`POST /api/backup` 调 `scheduler.backup_now()`，捕获 `BackupError` 后抛 `HTTPException(500, e.user_message)`。

- [ ] **Step 4: 运行全部测试，确认通过**

Run: `~/.local/bin/uv run pytest -q`
Expected: 全部 PASS；`git status --short` 不出现 `data/` 下的任何东西

- [ ] **Step 5: 提交**

```bash
git add mira/scheduler.py mira/main.py tests/test_scheduler.py tests/test_app.py
git commit -m "feat: daily automatic backup and backup-now endpoint"
```

---

### Task 3: 记忆页显示备份状态和「立即备份」按钮

**Files:**
- Modify: `web/memory.html`（状态 `<p>` 外包一层容器，旁边放按钮）
- Modify: `web/memory.js`（`loadStatus`、按钮点击处理）
- Modify: `web/style.css`（状态行与按钮的布局，复用现有按钮样式）

**Interfaces:**
- Consumes: `/api/memory-status` 的 `last_backup_at`、`backup_running`、`backup_error`；`POST /api/backup`（Task 2）

- [ ] **Step 1: 实现**
  - 状态文字末尾追加：`· 上次备份：${fmt(last_backup_at)}`；没有备份时显示 `· 还没有备份`；`backup_running` 为真时显示 `· 正在备份`；有 `backup_error` 时追加 `· ${backup_error}，30 分钟后自动重试`，并给状态区加上 `error` 样式。
  - 按钮 `<button id="backup-now" type="button">立即备份</button>`。点击后禁用按钮，文字改成"备份中…"，调用 `api("/api/backup", {method: "POST"})`。成功时 `toast("已备份：" + file)`，然后调用 `loadStatus()`；失败时 `api()` 已经弹出错误提示。最后（`finally`）恢复按钮。
  - 窄屏（≤ 600px）下按钮换到状态文字下面一行，不能出现横向滚动。

- [ ] **Step 2: 验证（开发模式）**

Run: `cd /Users/megu/Projects/Mira && MIRA_FAKE=1 ~/.local/bin/uv run python -c "from mira.config import load_settings; print(load_settings().db_path)"`
Expected: 输出以 `data/dev.db` 结尾

Run: `cd /Users/megu/Projects/Mira && MIRA_FAKE=1 PORT=8001 BACKUP_DIR=/private/tmp/claude-501/mira-dev-backups ~/.local/bin/uv run python -m mira`，浏览器打开 `http://127.0.0.1:8001/memory`
Expected: 状态区显示"上次备份：…"（来自启动时的那次备份）；点按钮后提示"已备份：dev-….db"；窗口缩到 400px 宽时布局不乱。

- [ ] **Step 3: 提交**

```bash
git add web/memory.html web/memory.js web/style.css
git commit -m "feat(web): show last backup and a backup-now button"
```

---

### Task 4: 桌面窗口入口 `mira/desktop.py`

**Files:**
- Create: `mira/desktop.py`
- Modify: `mira/__main__.py`（把日志初始化抽成 `setup_logging(log_file: Path | None)`，供两个入口共用）
- Modify: `pyproject.toml`、`uv.lock`（`uv add pywebview`）
- Test: `tests/test_desktop.py`

**Interfaces:**
- Consumes: `load_settings`、`ConfigError`、`create_app`、`EmbedderLoadError`、`port_in_use`（`mira/__main__.py`）
- Produces:
  - `probe_port(host: str, port: int) -> Literal["free", "mira", "other"]`：`"mira"` 的判断条件是 `GET http://{host}:{port}/api/memory-status` 在 2 秒内返回 200，且 JSON 里有 `"state"` 键（用标准库 `urllib.request`）。
  - `message_page(title: str, body: str) -> str`：返回完整 HTML，标题和正文都经过 `html.escape`；配色与 `web/style.css` 一致。`LOADING_PAGE = message_page("Mira 正在醒来…", "第一次启动需要下载约 90MB 的模型，请稍等")`。
  - `class ServerThread(threading.Thread)`：`__init__(self, settings: Settings)`；`run()` 内部先 `create_app` 再 `uvicorn.Server(...).run()`；属性 `ready: threading.Event`（在 `server.started` 为真后设置）、`error: str | None`（中文）；方法 `stop(timeout: float = 10) -> None`（设置 `should_exit` 并 `join`）。
  - `setup_logging(log_file: Path | None) -> None`：传 `None` 时输出到终端（现有行为）；传路径时用 `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)`，自动创建父目录。
  - `main() -> None`：日志写到 `settings.db_path.parent / "logs" / "mira.log"`；配置出错时，日志写到 `PROJECT_ROOT / "data" / "logs" / "mira.log"`。

- [ ] **Step 1: 写失败的测试** `tests/test_desktop.py`

```python
def test_probe_free_port(): ...                    # 绑定 0 号端口拿到一个空端口后关闭 → "free"
def test_probe_detects_running_mira(tmp_path):     # 在线程里用 uvicorn 启动 create_app(fake, tmp 路径) → "mira"
def test_probe_other_program():                    # http.server 在线程里返回 404 → "other"
def test_message_page_escapes_html():
    assert "<script>" not in message_page("<script>", "a & b") and "&amp;" in message_page("x", "a & b")
def test_server_thread_reports_chinese_error_on_bad_persona(tmp_path):
    # PERSONA_PATH 指向不存在的文件 → thread.join(10) 后 thread.error 是中文字符串，ready 没有被设置
def test_server_thread_starts_and_stops(tmp_path):
    # fake 设置 + 空闲端口 → ready.wait(15) 为真 → stop() → 端口变回 "free"，线程已结束
def test_setup_logging_writes_file(tmp_path): ...  # 写一条日志，确认 tmp_path/logs/mira.log 存在且含该内容；测完移除 handler
```

上面所有用到 `load_settings` 的测试都要带 `MIRA_FAKE=1`，并把 `DB_PATH`、`BACKUP_DIR`、`THEME_DIR` 指到 `tmp_path`。

- [ ] **Step 2: 运行测试，确认失败**

Run: `~/.local/bin/uv run pytest tests/test_desktop.py -q`
Expected: FAIL（`ModuleNotFoundError: mira.desktop`）

- [ ] **Step 3: 实现**
  - `uv add pywebview`。
  - `main()` 的流程：
    1. 读配置。出错就开一个窗口显示 `message_page("Mira 没能启动", str(e))`。
    2. `probe_port`：`"other"` → 窗口显示端口被占用的说明；`"mira"` → 直接打开 `http://host:port/`，不启动服务；`"free"` → 启动 `ServerThread`，窗口先显示 `LOADING_PAGE`。
    3. `webview.start(func=watch, ...)`。`watch` 会在 pywebview 自己的线程里执行：轮询 `ready` 或 `error`，再调用 `window.load_url(...)` 或 `window.load_html(message_page(...))`。
    4. `webview.start()` 返回（窗口关闭或 ⌘Q）后，如果服务是本进程启动的，调用 `ServerThread.stop()`。
  - 窗口参数：`title="Mira"`、`width=1280, height=860, min_size=(420, 600)`。
  - Dock 显示名字和图标：在 `webview.start()` 之前，用 pyobjc 设置 `NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "Mira"`。在 `watch` 里，如果 `theme/mira/avatar.png` 存在，就在主线程调用 `NSApplication.sharedApplication().setApplicationIconImage_(...)`，用 `AppKit` 的 `performSelectorOnMainThread` 或 `PyObjCTools.AppHelper.callAfter`。这一部分 import 失败或执行失败时只记日志，不能影响启动。

- [ ] **Step 4: 运行测试，确认通过**

Run: `~/.local/bin/uv run pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: 实测（开发模式，不碰真实库）**

Run: `cd /Users/megu/Projects/Mira && MIRA_FAKE=1 PORT=8001 BACKUP_DIR=/private/tmp/claude-501/mira-dev-backups ~/.local/bin/uv run python -m mira.desktop`

Expected（逐项记录实际看到的结果）：
- 窗口先显示"正在醒来"，然后进入聊天页。
- 发一条消息能收到假回复。
- 能进入记忆页。
- 视频背景能播放。
- 关窗后 `lsof -nP -iTCP:8001 -sTCP:LISTEN` 输出为空。
- 先在终端启动一个 8001 服务，再运行上面的命令：只开窗；关窗后终端里的服务仍在运行。
- 记录 Dock 显示的名字和图标。

- [ ] **Step 6: 提交**

```bash
git add mira/desktop.py mira/__main__.py pyproject.toml uv.lock tests/test_desktop.py
git commit -m "feat: desktop window entry point with loading and error pages"
```

---

### Task 5: 生成 `Mira.app` 的 `mira/make_app.py`

**Files:**
- Create: `mira/make_app.py`
- Test: `tests/test_make_app.py`

**Interfaces:**
- Consumes: `PROJECT_ROOT`（`mira/config.py`）
- Produces:
  - `BUNDLE_ID = "local.mira.desktop"`
  - `launcher_script(project_root: Path, uv_path: Path) -> str`：`#!/bin/zsh`，用 `shlex.quote` 包好路径，内容是 `cd <root> && exec <uv> run python -m mira.desktop`。
  - `info_plist() -> bytes`：用 `plistlib` 生成，包含 `CFBundleName`/`CFBundleDisplayName`=`"Mira"`、`CFBundleIdentifier`=`BUNDLE_ID`、`CFBundleExecutable`=`"Mira"`、`CFBundlePackageType`=`"APPL"`、`CFBundleIconFile`=`"Mira"`、`NSHighResolutionCapable`=`True`。
  - `make_icns(png: Path, out: Path) -> bool`：用 `sips -z` 生成 16/32/128/256/512 及对应的 @2x 尺寸，放进临时的 `.iconset`，再用 `iconutil -c icns` 打包。任何一步失败都返回 `False`，不抛异常。
  - `build_app(dest: Path, project_root: Path, uv_path: Path, icon_png: Path | None) -> Path`：目标已存在且 `Contents/Info.plist` 的 `CFBundleIdentifier` 等于 `BUNDLE_ID` 时先删再建；目标已存在但标识不同或读不出来时，抛 `FileExistsError`（中文信息）。启动脚本设为 `0o755`。
  - `main() -> None`：`uv` 路径取 `shutil.which("uv")`，找不到就用 `~/.local/bin/uv`，都不存在时打印中文错误并退出 1；`dest = ~/Applications/Mira.app`（自动创建 `~/Applications`）；图标取 `theme/mira/avatar.png`，存在才用；最后打印"已生成 …，可以拖进 Dock"。

- [ ] **Step 1: 写失败的测试** `tests/test_make_app.py`

```python
def test_build_app_structure(tmp_path):
    app = build_app(tmp_path / "Mira.app", Path("/x/我的 项目"), Path("/u/uv"), None)
    plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert plist["CFBundleIdentifier"] == "local.mira.desktop" and plist["CFBundleExecutable"] == "Mira"
    exe = app / "Contents/MacOS/Mira"
    assert exe.stat().st_mode & 0o111
    assert "'/x/我的 项目'" in exe.read_text() and "mira.desktop" in exe.read_text()

def test_launcher_runs_under_shell_with_spaces(tmp_path):
    # 造一个带空格的目录，里面放一个假 uv 脚本，把参数和 $PWD 写进文件；执行启动脚本后，断言 PWD 和参数都正确
def test_rebuild_overwrites_own_app(tmp_path): ...       # 连续 build 两次都成功；第一次留下的多余文件被清掉
def test_refuses_to_overwrite_foreign_app(tmp_path):
    foreign = tmp_path / "Mira.app/Contents"; foreign.mkdir(parents=True)
    (foreign / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.other.app"}))
    with pytest.raises(FileExistsError): build_app(tmp_path / "Mira.app", tmp_path, Path("/u/uv"), None)
    assert (foreign / "Info.plist").exists()
def test_make_icns_returns_false_on_bad_png(tmp_path): ...  # 写一个不是图片的 .png → False，不抛异常

@pytest.mark.skipif(sys.platform != "darwin", reason="需要 sips/iconutil")
def test_make_icns_from_real_png(tmp_path): ...  # 用 zlib+struct 生成一张 64×64 纯色 PNG → True，输出文件非空
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `~/.local/bin/uv run pytest tests/test_make_app.py -q`
Expected: FAIL（`ModuleNotFoundError: mira.make_app`）

- [ ] **Step 3: 实现**（按 Interfaces 写，不需要额外的设计决定）

- [ ] **Step 4: 运行测试，确认通过**

Run: `~/.local/bin/uv run pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add mira/make_app.py tests/test_make_app.py
git commit -m "feat: generate a Mira.app launcher with your avatar as icon"
```

---

### Task 6: README 与整体实测

**Files:**
- Modify: `README.md`（"启动"一节加 App 用法，"日常使用"里的备份条目改写为自动备份 + 恢复步骤，"代码结构"表加 `backup.py`、`desktop.py`、`make_app.py`）

- [ ] **Step 1: 更新 README**
  - **App**：`uv run python -m mira.make_app` → 在"应用程序"里打开 Mira → 可以拖进 Dock。关窗口就停止。日志在 `data/logs/mira.log`。移动项目文件夹后需要重新运行一次。终端启动方式仍然可用。
  - **备份**：每天自动备份到 `data/backups/`，保留 14 份，可在 `.env` 里改。记忆页可以手动立即备份。恢复步骤：退出 Mira → 用备份文件覆盖 `data/mira.db` → 删除 `data/mira.db-wal`、`data/mira.db-shm`。
  - 把 Task 4 实测得到的 Dock 名字/图标结果如实写进去，如果有限制要写明。

- [ ] **Step 2: 完整验证**

Run: `cd /Users/megu/Projects/Mira && ~/.local/bin/uv run pytest -q && node --test tests/web/sync.test.js`
Expected: 全部通过

Run: 把 `build_app` 生成到草稿目录（不碰 `~/Applications`），然后执行 `MIRA_FAKE=1 PORT=8001 BACKUP_DIR=/private/tmp/claude-501/mira-dev-backups open <草稿目录>/Mira.app`
Expected: 从 Finder 方式启动也能出现窗口并进入聊天页（验证不依赖 shell PATH）。注意 `open` 不传递环境变量；如果无法以开发模式启动，就改为直接执行 `Contents/MacOS/Mira`，并在汇报里写明"Finder 启动路径未用开发库验证，留给用户首次真实使用时确认"。

Run: `git status --short`
Expected: 只有 `README.md`

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: desktop app and automatic backups"
```

- [ ] **Step 4: 交给用户**：给出一行完整命令 `cd /Users/megu/Projects/Mira && ~/.local/bin/uv run python -m mira.make_app`，由用户自己生成真实的 App，并告诉用户首次打开时要看哪几项。
