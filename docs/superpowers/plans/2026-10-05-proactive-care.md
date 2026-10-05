# Mira 主动关心（主动消息）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mira 在值得时主动开口：打开 App 先说、Mac 系统通知、每周信（周日 20:00）。不烦人、不花钱乱调用、`PROACTIVE=0` 可整体关掉。

**Architecture:** 新模块 `mira/proactive.py`（`ProactiveEngine`：纯规则闸门 + 候选收集 + 一次模型决策），作为后台任务接入现有 `Scheduler`（复用主锁、30 分钟退避、`job_state`）。主动消息就是普通助手消息（`meta.proactive` 标记），经 `ChatEngine.announce_proactive()` 用现有 WS 事件（`typing`/`bubble`）广播，**聊天页零改动**。通知在 `mira/notify.py`（`MacNotifier`/`NullNotifier`），由 `create_app(notifier=…)` 注入，只在 Mira.app（bundle）里启用。

**Tech Stack:** 现有栈；pyobjc 的 `UserNotifications`（pywebview 自带 pyobjc，无新依赖）。

**Spec:** `docs/superpowers/specs/2026-10-05-proactive-care-design.md`

## Global Constraints

- 开发/测试**绝不碰** `data/mira.db` 和真实的 `data/backups/`；测试里任何 `create_app` 都把 `DB_PATH`、`BACKUP_DIR` 指到 `tmp_path`；`MIRA_FAKE=1` 时 `PROACTIVE` 默认关闭（要用就显式 `PROACTIVE=1`）。
- **花钱路径**：闸门与候选收集全是纯规则；没有候选时**一次模型都不调**；模型失败走现有 30 分钟退避；所有测试用 `FakeLLM`/`EchoLLM`，不联网。
- 提交只 `git add <具体路径>`，不用 `-A`/`.`；提交前看 `git status --short`，出现 `data/`、`theme/`、`*.local.md`、媒体文件就停下；提交信息**不加** `Co-Authored-By`；作者用仓库本地配置。
- 面向用户的文字一律中文；时间比较一律用带时区的 `clock.now()`。
- 测试命令：`cd /Users/megu/Projects/Mira && ~/.local/bin/uv run pytest -q`；前端 `node --test tests/web/sync.test.js`（本计划前端零改动，收尾时跑一遍确认）。
- 主动消息**不调用** `scheduler.notify_activity()`：它是"用户活动"语义，会影响写入器空闲计时。

## Review Focus

1. **不烦人**：闸门的每一条边界都要有测试——4 小时在场间隔、22-8 安静时段（可跨夜）、每天 1 条、评估间隔 4 小时、"没人应的主动消息不追问"、同承诺 3 天 / 同目标 14 天 / 事件只回访一次。任何一条被绕过都算 bug。
2. **不花钱**：`ready()`/候选收集纯规则；只有候选非空才调模型；`LLMBadJSON` 按沉默处理（不重试），其他 `LLMError` 才退避；测试断言"沉默场景里 FakeLLM 一次都没被调用"。
3. **别插话**：模型调用后、开口前必须复查（"在场/没回应/正在聊"三项重查），任何一项不过就放弃——不存消息、不写日志、下次评估再说。
4. **写入器联动不改变现有行为**：助手消息仍要跟用户消息一起被整理；只有"全是助手消息（主动消息）"时不触发、不算"等待中"；现有 339 passed 必须保持。
5. **通知失败隔离**：通知器抛异常绝不能影响"消息已落库/已广播"；`NOTIFICATIONS=0`、终端模式、非 bundle、权限被拒都静默降级（最多记一次日志）。
6. **数据完整性**：消息先落库再广播；`proactive_log` 只在真的开口之后写；测试与评测用 `:memory:`/`tmp_path`。

## 依赖顺序

Task 1 → 2 → 3 → 4 → 5（**第一步完成，已可用**）→ 6 → 7（第二步：通知）→ 8 → 9（第三步：每周信 + 评测收尾）。每步可单独提交、单独退。

---

### Task 1: 配置与存储地基

**Files:**
- Modify: `mira/config.py`（6 个新字段、布尔解析、校验）
- Modify: `mira/store.py`（`proactive_log` 表、`SCHEMA_VERSION=2`、6 个新方法）
- Modify: `.env.example`
- Test: `tests/test_config.py`、`tests/test_store_messages.py`、`tests/test_store_proactive.py`（新）

**Interfaces:**
- Produces（`Settings` 新字段）：
  - `proactive: bool = True`（`PROACTIVE`；开发模式默认 `False`）
  - `proactive_max_per_day: int = 1`（`PROACTIVE_MAX_PER_DAY`，必须 ≥1）
  - `proactive_quiet_hours: str = "22-8"`（`PROACTIVE_QUIET_HOURS`，`开始-结束`，0–23，允许跨夜）
  - `weekly_letter_weekday: int = 6`、`weekly_letter_hour: int = 20`（0=周一 … 6=周日）
  - `notifications: bool = True`（`NOTIFICATIONS`）
- Produces（`Store` 新方法）：
  - `add_proactive_log(kind: str, ref_type: str | None = None, ref_id: int | None = None) -> None`
  - `last_proactive_at() -> datetime | None`（含 `weekly_letter`）
  - `proactive_count_on(day: date) -> int`（**不含** `weekly_letter`）
  - `last_proactive_ref(ref_type: str, ref_id: int) -> datetime | None`
  - `unprocessed_user_messages() -> list[Message]`（`role='user' AND processed=0`，按 id 升序）
  - `pending_user_message_count() -> int`
  - `latest_user_message(exclude_batch: str | None = None) -> Message | None`

- [ ] **Step 1: 写失败的测试**

`tests/test_store_proactive.py`（新）：

```python
def test_proactive_log_roundtrip(store, clock):
    store.add_proactive_log("commitment", "commitment", 7)
    assert store.last_proactive_at() == clock.now()
    assert store.last_proactive_ref("commitment", 7) == clock.now()
    assert store.last_proactive_ref("commitment", 8) is None

def test_count_excludes_weekly_letter(store, clock):
    store.add_proactive_log("missing")
    store.add_proactive_log("letter")
    assert store.proactive_count_on(clock.now().date()) == 1
    clock.advance(86400)
    assert store.proactive_count_on(clock.now().date()) == 0

def test_schema_version_is_2(store):
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 2  # 或走公开方法
```

`tests/test_store_messages.py` 追加：

```python
def test_unprocessed_user_messages_ignores_assistant(store):
    store.add_message("user", "hi")
    store.add_message("assistant", "在", meta={"proactive": {"kind": "missing"}})
    assert [m.role for m in store.unprocessed_user_messages()] == ["user"]
    assert store.pending_user_message_count() == 1

def test_latest_user_message_skips_assistant_and_batch(store):
    store.add_message("user", "一", batch_id="b1")
    store.add_message("assistant", "二")
    assert store.latest_user_message().content == "一"
    assert store.latest_user_message(exclude_batch="b1") is None
```

`tests/test_config.py` 追加：

```python
def test_proactive_defaults():
    s = load_settings({"DEEPSEEK_API_KEY": "k"})
    assert s.proactive is True and s.proactive_max_per_day == 1
    assert s.proactive_quiet_hours == "22-8" and s.notifications is True
    assert (s.weekly_letter_weekday, s.weekly_letter_hour) == (6, 20)

def test_proactive_off_in_fake_and_explicit_on():
    assert load_settings({"MIRA_FAKE": "1"}).proactive is False
    assert load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"}).proactive is True
    assert load_settings({"DEEPSEEK_API_KEY": "k", "PROACTIVE": "0"}).proactive is False

@pytest.mark.parametrize("key,value", [("PROACTIVE_QUIET_HOURS", "25-8"), ("PROACTIVE_QUIET_HOURS", "abc"),
                                       ("WEEKLY_LETTER_WEEKDAY", "7"), ("WEEKLY_LETTER_HOUR", "24"),
                                       ("PROACTIVE_MAX_PER_DAY", "0")])
def test_proactive_invalid_values(key, value): ...  # 抛 ConfigError
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `~/.local/bin/uv run pytest tests/test_config.py tests/test_store_messages.py tests/test_store_proactive.py -q`
Expected: FAIL（字段/方法不存在）

- [ ] **Step 3: 实现**

- `config.py`：布尔字段不能走 `f.type(raw)`（`bool("0") == True`）。像 `fake` 一样特判：`if f.name in ("proactive", "notifications"): values[f.name] = raw == "1"; continue`；在 `fake` 默认块里加 `if values.get("fake") and "proactive" not in values: values["proactive"] = False`。
- 校验（`load_settings` 末尾）：`_parse_quiet_hours("22-8") -> tuple[int, int]`（解析失败/越界抛 `ConfigError`，中文提示）；`weekly_letter_weekday` 0–6；`weekly_letter_hour` 0–23；`proactive_max_per_day >= 1`。
- `store.py`：SCHEMA 追加 `CREATE TABLE IF NOT EXISTS proactive_log(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref_type TEXT, ref_id INTEGER, created_at TEXT NOT NULL);`；`SCHEMA_VERSION = 2`（旧库靠 `executescript` 自动补表，无需迁移）。
- `proactive_count_on` 在 Python 侧按 `created_at.date() == day` 过滤（存的是本机时区 ISO 串，别用 SQL `date()` 的 UTC 语义）。
- `.env.example` 追加六项及中文注释，注明"`PROACTIVE=0` 全部关闭"。

- [ ] **Step 4: 运行全部测试**

Run: `~/.local/bin/uv run pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add mira/config.py mira/store.py .env.example tests/test_config.py tests/test_store_messages.py tests/test_store_proactive.py
git commit -m "feat(proactive): config keys and store groundwork"
```

---

### Task 2: 规则层——闸门 + 候选（`mira/proactive.py`）

**Files:**
- Create: `mira/proactive.py`
- Test: `tests/test_proactive_rules.py`（新）

**Interfaces:**
- Produces:
  - 常数：`MIN_USER_GAP = timedelta(hours=4)`、`EVAL_GAP = timedelta(hours=4)`、`IDLE_GAP = timedelta(days=2)`、`EPISODE_WINDOW = timedelta(hours=72)`、`COMMITMENT_COOLDOWN = timedelta(days=3)`、`GOAL_COOLDOWN = timedelta(days=14)`、`GOAL_STALE = timedelta(days=14)`
  - `quiet_now(hours: str, now: datetime) -> bool`（纯函数；支持跨夜）
  - `@dataclass Candidate(kind: str, ref_type: str | None, ref_id: int | None, text: str)`；kind ∈ `{"commitment", "missing", "checkin", "goal"}`
  - `class ProactiveEngine`：`__init__(*, store, llm, retriever, settings, engine, persona: str, rules: str, notifier=None, now=clock.now)`（`engine` 是 `ChatEngine`，本任务只用到 `engine.busy()`——为方便测试可以传任意有 `busy()` 的对象）
  - `ProactiveEngine.ready() -> bool`（闸门，全部纯规则）
  - `ProactiveEngine.collect() -> list[Candidate]`

**闸门顺序（`ready()`，任意一条不过就 False）：**
1. `settings.proactive`
2. `not quiet_now(settings.proactive_quiet_hours, now)`
3. 存在至少一条用户消息（全新安装不主动）
4. `now - latest_user_message().created_at >= MIN_USER_GAP`（人在场不说话）
5. 评估间隔：`store.get_job_last_run("proactive")` 为空或距今 `>= EVAL_GAP`
6. 当天上限：`store.proactive_count_on(now.date()) < settings.proactive_max_per_day`
7. 有人应才继续：`store.last_proactive_at() is None or last_proactive_at <= latest_user_message().created_at`（"没人应的主动消息不追问"）
8. `not engine.busy()`（不插话）

**候选（`collect()`，全部带 `#id` 文本，一行一条）：**
- `commitment`：`store.open_commitments()` 中 `due_at is not None and due_at <= now.date()`，且 `last_proactive_ref("commitment", m.id)` 为空或距今 `>= COMMITMENT_COOLDOWN`；文本注明逾期天数。
- `missing`：距最后用户消息 `>= IDLE_GAP` → 一条（`ref_type=None`）。
- `checkin`：`store.list_memories("episode")` 中 `created_at >= now - EPISODE_WINDOW`，且 `last_proactive_ref("episode", m.id)` 为空（**只回访一次**）。
- `goal`：`store.open_goals()` 中 `now - m.updated_at >= GOAL_STALE`，且 `last_proactive_ref("goal", m.id)` 为空或距今 `>= GOAL_COOLDOWN`。

- [ ] **Step 1: 写失败的测试** `tests/test_proactive_rules.py`

复用 `store`、`clock` fixtures 和 `load_settings({"MIRA_FAKE": "1", "PROACTIVE": "1"})`；`engine` 用一个 `Busy` 小类（`busy()` 返回可控布尔）。

```python
def test_quiet_now_edges():          # "22-8"：21:59 False、22:00 True、07:59 True、08:00 False；"8-17" 不跨夜
async def test_ready_blocks(...):    # 开关关 / 安静时段 / 没有用户消息 / 4h 内说过话 / 评估间隔内 / 今天已开口 / 上条主动没被回 / busy —— 各一条测试
def test_ready_allows_after_4h(...)
def test_collect_commitment_due_and_cooldown(...)   # 逾期在列；未来不在；2 天前提醒过不在、4 天前提醒过在列
def test_collect_missing_gap(...)                   # 1 天不在、2 天在
def test_collect_episode_once(...)                  # 71h 在、73h 不在、已回访过不在
def test_collect_goal_stale_and_cooldown(...)       # 13 天不在、15 天在；10 天前推过不在、15 天前推过在
def test_collect_empty(...)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `~/.local/bin/uv run pytest tests/test_proactive_rules.py -q`
Expected: FAIL（`ModuleNotFoundError: mira.proactive`）

- [ ] **Step 3: 实现** `mira/proactive.py`（只写常数、`quiet_now`、`Candidate`、`ProactiveEngine.__init__/ready/collect`；`run()` 留在 Task 3/4）

- [ ] **Step 4: 运行全部测试** — Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add mira/proactive.py tests/test_proactive_rules.py
git commit -m "feat(proactive): rule gate and candidate collection"
```

---

### Task 3: 提示词与决策解析

**Files:**
- Create: `mira/prompts/proactive.md`
- Modify: `mira/proactive.py`（`Decision`、`parse_decision`、`async decide()`）
- Modify: `mira/fakes.py`（`EchoLLM` 加 `proactive` 分支）
- Test: `tests/test_proactive_decision.py`（新）

**Interfaces:**
- Produces:
  - `@dataclass Decision(speak: bool, kind: str, reason: str, ref_id: int | None, messages: list[str], expression: str | None)`
  - `parse_decision(data: dict, candidates: list[Candidate]) -> Decision | None`（纯函数）
  - `ProactiveEngine.decide() -> Decision | None`：`system` = 与聊天相同的 `persona + rules`；`user` = `render("proactive", now=…, gap=…, transcript=…, candidates=…, memories=…)` → `llm.complete_json(purpose="proactive", model=settings.chat_model, messages=…, max_tokens=1200, temperature=settings.chat_temperature)` → `parse_decision`
- 校验规则：`speak` 非真 → `None`；`messages` 取非空字符串、`clip_text(…, 300)`、最多 3 条，空则 `None`；`kind` 必须在候选 kind 集合里；`ref_id` 必须等于该 kind 候选中某个的 `ref_id`（`missing` 允许 `None`），对不上 → `None`。
- 错误：`LLMBadJSON` → 记日志、返回 `None`（按沉默处理，不回退不重试）；其他 `LLMError` 往上抛（由调度器退避）。
- `mira/prompts/proactive.md`（`render()` 只替换 `{{key}}`，JSON 示例里的单大括号原样保留）：

```markdown
【这次不是回复，是判断要不要主动开口】

现在是 {{now}}，距对方上次说话已经 {{gap}}。

最近对话：
{{transcript}}

可以考虑开口的候选（最多选一件；拿不准就都放弃）：
{{candidates}}

你记得的相关事情：
{{memories}}

要求：
- 沉默是完全正常的。为说而说是最糟的结果。
- 一次只说一件事，挑最值得现在说的那一件；不要追问，不要说教，不要编造。
- 如果要说：像平时发消息一样，1–3 条短气泡，一句一条。
- kind 和 ref_id 从候选表里原样照抄；情绪回访选事件候选。

只输出 JSON：
{"speak": true, "kind": "commitment", "ref_id": 12, "reason": "为什么值得现在说",
 "messages": ["…"], "expression": "表情英文名"}
```

- `EchoLLM`：`if purpose == "proactive": return {"speak": False, "reason": "开发模式"}`。

- [ ] **Step 1: 写失败的测试** `tests/test_proactive_decision.py`

```python
def test_parse_decision_valid(...)            # speak/kind/ref_id 都对 → Decision，messages 截断与限 3 条
def test_parse_decision_rejections(...)       # speak 缺失/False → None；messages 全空 → None；kind 不在候选 → None；ref_id 对不上 → None
async def test_decide_uses_chat_model_and_purpose()
async def test_decide_silent_without_candidates_or_speech()   # 无候选 / speak=False / LLMBadJSON → None
async def test_decide_without_candidates_makes_no_call(store, clock)
    # FakeLLM(script=[])；candidates 置空后 decide() → None，且 llm.calls == []
```

- [ ] **Step 2: 运行测试，确认失败** → **Step 3: 实现** → **Step 4: 全部测试 PASS**

- [ ] **Step 5: 提交**

```bash
git add mira/prompts/proactive.md mira/proactive.py mira/fakes.py tests/test_proactive_decision.py
git commit -m "feat(proactive): proactive prompt and decision parsing"
```

---

### Task 4: 开口通道 + 调度接线

**Files:**
- Modify: `mira/chat.py`（`busy()`、`announce_proactive()`；`last_chat_at` 改用最后一条用户消息）
- Modify: `mira/proactive.py`（`run()`）
- Modify: `mira/scheduler.py`（新任务、启动顺序、`status()` 的 pending 语义）
- Modify: `mira/main.py`（构建 `ProactiveEngine` 传给 `Scheduler`）
- Test: `tests/test_proactive_flow.py`（新）、`tests/test_scheduler.py`、`tests/test_chat.py`

**Interfaces:**
- Produces:
  - `ChatEngine.busy() -> bool`：`self._task is not None and not self._task.done()`
  - `async ChatEngine.announce_proactive(texts: list[str], meta: dict, expression: str | None = None) -> bool`：`busy()` → `False`；否则逐条 `typing` → `self._sleep(bubble_delay(text))` → `add_message("assistant", text, meta=meta if 第一条 else None)` → 广播 `bubble`（带 `expression`）；**不调用 `_on_activity()`**；返回 `True`。
  - `ProactiveEngine.run()`：`decide()` → 复查（闸门 4/7/8 重查：`_recheck()`）→ `announce_proactive` → `store.add_proactive_log(kind, ref_type, ref_id)` → 通知（Task 6 接进来；现在是空钩子）。
  - `Scheduler(..., proactive=None)`；`_proactive_due()` = `self._proactive is not None and self._proactive.ready()`；`startup()`：writer → **proactive** → reflector；`tick()`：writer → **proactive** → reflector → backup；任务名 `"proactive"`（主锁 + 30 分钟退避，错误只记日志）。
  - `chat.py`：`previous = self._store.latest_user_message(exclude_batch=batch_id)`。
  - `scheduler.status()`：`pending = self._store.pending_user_message_count()`；`startup()`/`tick()` 的写入器触发同样改用 `unprocessed_user_messages()`。
  - `main.py`：`ProactiveEngine(store=…, llm=…, retriever=…, settings=…, engine=engine, persona=…, rules=…)`，传给 `Scheduler`。

- [ ] **Step 1: 写失败的测试**

`tests/test_proactive_flow.py`（用真 `ChatEngine` + `HashEmbedder` + `FakeLLM` + 假 send 收集事件；`sleep` 传 `lambda s: asyncio.sleep(0)`；THEME 不传）：

```python
async def test_announce_broadcasts_typing_then_bubbles(...)   # 事件顺序 typing,bubble,typing,bubble；第一条带 meta、后面 meta 为 None
async def test_announce_skips_when_busy(...)                  # 塞一个未完成的 _task → False，无消息落库
async def test_run_full_path(...)                             # FakeLLM 说 speak=true → 消息落库(meta.proactive) + proactive_log 一行
async def test_run_drops_when_user_returns_midcall(...)       # 自定义 LLM：complete_json 里先 add_message("user",…) 再返回 speak=true → 不落库、不记 log
```

`tests/test_scheduler.py`（`make()` 加 `proactive=None` 参数）：

```python
async def test_startup_order_writer_proactive_reflector(...)  # 各任务记录调用次序
async def test_proactive_runs_after_eval_gap(...)             # job_state("proactive") 4h 内 → 不跑；过后 → 跑
async def test_proactive_skipped_when_not_ready(...)          # ready() False（比如 4h 内说过话）→ 不跑
async def test_proactive_none_keeps_behaviour(...)            # 不传 proactive → 与现在一致
async def test_writer_ignores_assistant_only_pending(...)     # 只有未处理助手消息 → tick 后 writer.runs == 0，status 的 pending_messages == 0
```

`tests/test_chat.py` 补一条：`last_chat_at` 用的是最后一条用户消息（主动消息在最后时，聊天上下文里的"距上次聊天"仍按用户消息算）。

- [ ] **Step 2: 运行测试，确认失败** → **Step 3: 实现** → **Step 4: 全量测试 PASS，`git status --short` 干净**

- [ ] **Step 5: 提交**

```bash
git add mira/chat.py mira/proactive.py mira/scheduler.py mira/main.py tests/test_proactive_flow.py tests/test_scheduler.py tests/test_chat.py
git commit -m "feat(proactive): deliver proactive messages through the chat engine"
```

---

### Task 5: 第一步实测 + 文档

- [ ] **Step 1: 开发模式机制实测**（不碰真实库）

Run: `cd /Users/megu/Projects/Mira && MIRA_FAKE=1 ~/.local/bin/uv run python -c "from mira.config import load_settings; print(load_settings().db_path)"`
Expected: 以 `data/dev.db` 结尾

Run: `cd /Users/megu/Projects/Mira && MIRA_FAKE=1 PROACTIVE=1 PORT=8001 ~/.local/bin/uv run python -m mira`，另开一个终端用一段 `python -c` 小脚本（同一 `dev.db` + `FakeLLM([{"speak": True, "kind": "missing", "messages": ["（开发模式的主动消息）"], "reason": "test"}])` + 真 `ChatEngine` + `asyncio`）触发一次开口。
Expected: 浏览器 `http://127.0.0.1:8001` 聊天页实时冒出气泡（typing → 气泡）；`/api/memory-status` 的 `pending_messages` 仍为 0（主动消息不算待整理）。

- [ ] **Step 2: 真实端到端（需要用户同意）**

告诉用户：等真实库里有"今天到期/逾期"的承诺、且 4 小时没聊天时，打开 Mira.app 应看到 Mira 先开口。**由用户决定什么时候验**；效果如实记录（这一条实际写进真实聊天记录，属功能本身行为）。

- [ ] **Step 3: README**

"功能"列表加"主动关心（打开 App 时 Mira 可能先说话）"；配置表加六个键（一句中文说明）；注明 `PROACTIVE=0` 关闭、安静时段与每天上限可调。

- [ ] **Step 4: 全量测试 + 提交**

```bash
git add README.md
git commit -m "docs: proactive care phase 1 (open greeting)"
```

- [ ] **Step 5: 给用户**：告诉用户第一步已可用、怎么观察、怎么关；第二步（通知）继续。

---

### Task 6: 通知模块与接线

**Files:**
- Create: `mira/notify.py`
- Modify: `mira/desktop.py`（构造 `MacNotifier` + 点击激活窗口的回调）、`mira/main.py`（`create_app(settings, *, llm=None, embedder=None, notifier=None)`）、`mira/proactive.py`（开口成功后通知）
- Test: `tests/test_notify.py`（新）、`tests/test_proactive_flow.py`（补）

**Interfaces:**
- Produces:
  - `class NullNotifier: def notify(self, title: str, body: str) -> None: pass`
  - `class MacNotifier: def __init__(self, on_click=None)`：懒加载 pyobjc（`UserNotifications`）；首次 `requestAuthorizationWithOptions_`；`NSObject` delegate 持有引用、点击后 `AppHelper.callAfter(on_click)`；`notify()` 任何异常只记日志。
  - `make_notifier(*, notifications: bool, fake: bool, on_click=None) -> NullNotifier | MacNotifier`：`fake` / `notifications=False` / 非 bundle（`NSBundle.mainBundle().bundleIdentifier() != "local.mira.desktop"`）/ 导入失败 → `NullNotifier`。
  - `ProactiveEngine.run()` 末尾：`if self._notifier: self._notifier.notify("Mira", clip_text(第一条, 120))`，整体 try/except。
  - 开发排查：bundle 模式且 `MIRA_TEST_NOTIFY=1` 时，`desktop.py` 启动后发一条"测试通知"（不写库）。

- [ ] **Step 1: 写失败的测试**：`make_notifier` 各条件组合返回 `NullNotifier`；FakeNotifier 注入后 speak=true 恰好一次；沉默时零次；`NOTIFICATIONS=0` 零次；notifier 抛异常 → 消息仍落库、异常不外泄。

- [ ] **Step 2-4: 失败确认 → 实现 → 全量 PASS**

- [ ] **Step 5: 提交**

```bash
git add mira/notify.py mira/desktop.py mira/main.py mira/proactive.py tests/test_notify.py tests/test_proactive_flow.py
git commit -m "feat(proactive): macos notifications from the app bundle"
```

---

### Task 7: 通知真机实测（含备选方案）

- [ ] **Step 1:** 让用户运行 `cd /Users/megu/Projects/Mira && ~/.local/bin/uv run python -m mira.make_app` 生成 App；用 `MIRA_TEST_NOTIFY=1` 启动（终端里 `MIRA_TEST_NOTIFY=1 ~/Applications/Mira.app/Contents/MacOS/Mira`）确认：权限弹窗、通知外观与标题、点通知是否回到窗口。
- [ ] **Step 2:** 结果如实记录进 README（"已知限制"）与 `HANDOFF.local.md`。
- [ ] **Step 3:** 如果发不出通知：实现备选——Dock 图标跳动（`NSApplication.requestUserAttention_`）+ Dock 徽标数字，点 Dock 回到窗口；通知行为文档里改成"打开 Mira 看"。
- [ ] **Step 4:** 提交（`docs: record macos notification verification` 或修复用 `fix(proactive): …`）。

---

### Task 8: 每周信

**Files:**
- Create: `mira/prompts/weekly.md`
- Modify: `mira/proactive.py`（`weekly_ready()`、`run_weekly()`、`_weekly_target()`）、`mira/scheduler.py`（第二个任务 `"weekly_letter"`；`_run` 支持"返回 False 不记 `job_state`"）、`mira/fakes.py`（`weekly` 分支返回固定两条）
- Test: `tests/test_weekly_letter.py`（新）、`tests/test_scheduler.py` 补顺序

**Interfaces:**
- Produces:
  - `_weekly_target(now, weekday, hour) -> datetime`：最近一个"该发时刻"——若 `now` 已过本周的那天那点就是本周的，否则是上周的。
  - `weekly_ready() -> bool`：`settings.proactive` → `last = store.get_job_last_run("weekly_letter")` 为空或 `last < _weekly_target(...)` → 且 `last` 距今 `>= 6 天` → 且 `not engine.busy()`。
  - `async run_weekly() -> bool`：素材 = 最近 7 天的 `episode`/新建记忆 + `open_commitments()`/`open_goals()` + 档案 → `purpose="weekly"`、`max_tokens=1500` → 校验 `messages`（2–4 条、`clip_text(…, 500)`）→ 复查 busy → `announce_proactive(meta={"proactive": {"kind": "letter"}})` → `add_proactive_log("letter")` → 通知 → `True`；复查不过 / announce 返回 `False` → **返回 `False`**（下个 tick 重试）。
  - `Scheduler._run`：`result = await job()`；`result is not False` 才 `set_job_last_run`（现有任务返回 `None`，行为不变）。
  - 调度顺序（startup 与 tick 相同）：writer → **weekly**（发出一封就跳过本轮的 proactive）→ proactive → reflector → backup。每周信不受每天上限/冷却/安静时段约束，受 master 开关。
  - `mira/prompts/weekly.md`：变量 `now / week_events / profile / open_items`；要求：像一封信，2–4 条短气泡，这周印象最深的一两件事、看到的变化、一句下周的关心或提醒；不流水账、不编造、不说教、不提问；输出 `{"messages": ["…"], "expression": "…"}`。

- [ ] **Step 1: 写失败的测试**

```python
def test_weekly_target_math(...)        # 周日 19:59 → 上周日 20:00；20:01 → 本日；周一 → 昨天
async def test_weekly_due_and_catchup(...)   # 首发在周日 20:01；<6 天不再发；睡过 Sunday 周一开机补发
async def test_weekly_retries_when_busy(...) # run_weekly 返回 False → job_state 未写 → 下个 tick 再试成功
async def test_letter_skips_proactive_this_pass(...)  # 同一次 tick 里发过 letter → proactive.runs == 0
async def test_weekly_parse_and_meta(...)    # 消息 meta kind == "letter"；EchoLLM weekly 分支可跑
```

- [ ] **Step 2-4: 失败确认 → 实现 → 全量 PASS**

- [ ] **Step 5: 提交**

```bash
git add mira/prompts/weekly.md mira/proactive.py mira/scheduler.py mira/fakes.py tests/test_weekly_letter.py tests/test_scheduler.py
git commit -m "feat(proactive): weekly letter"
```

---

### Task 9: 评测 + 收尾

**Files:**
- Modify: `evals/run_evals.py`（支持主动消息/每周信两组场景，沿用现有 `✗/✓ + 通过 N/M` 输出与 `--yes`）
- Create: `evals/scenarios_proactive/*.json`（4 个）与每周信场景 1 个
- Modify: `README.md`、`docs/superpowers/specs/2026-10-05-proactive-care-design.md`（状态行）、`HANDOFF.local.md`（不在 git）

- [ ] **Step 1: 评测驱动 + 场景**

场景（`:memory:` Store + 固定时钟 + 真 LLM/embedder；断言 `speak`、`messages` 包含关键词、以及**模型调用次数**）：
a) 承诺逾期 3 天 + 两天没聊 → 开口，只提这一件；
b) 风平浪静（无候选）→ 沉默，且 **0 次调用**；
c) 前一天的情绪事件 → 温暖回访（`kind == "checkin"`）；
d) 一小时前刚聊过 → 沉默，**0 次调用**（纯规则）；
e) 每周信：给一周素材 → 2–4 条、含某件真事、不编造。

- [ ] **Step 2: 假模型自检**：先用 `FakeLLM` 跑一遍断言自洽（不花钱）。
- [ ] **Step 3: 真实 API 运行（先问用户，约几分钱）**

Run: `cd /Users/megu/Projects/Mira && ~/.local/bin/uv run python evals/run_evals.py`
Expected: 原有 15 个场景保持全过；新场景按上面断言。不过就调提示词再跑，结果记进交接文档。

- [ ] **Step 4: 收尾文档**：README 完整化（功能、配置表、通知与每周信说明、"关掉"的说明）；spec 状态行改"已实现"；HANDOFF §2/§11 更新；给用户一段"怎么验收"的步骤。
- [ ] **Step 5: 提交**

```bash
git add evals/run_evals.py evals/scenarios_proactive README.md docs/superpowers/specs/2026-10-05-proactive-care-design.md
git commit -m "docs: proactive care complete (notifications, weekly letter, evals)"
```

---

## 完成定义

- 全部测试通过（预计新增 40–60 条；原 339 passed + 1 skipped 保持），前端 9 passed。
- 开发模式（假模型）能脚本触发一次"开口"并在聊天页看到。
- 真实环境：有到期承诺且 4 小时没聊时，打开 Mira.app 会先说话；周日 20:00（或补发）收到每周信；系统通知能弹（或按 Task 7 记录的备选方案工作），点击回到窗口。
- `PROACTIVE=0` 后行为与实现前一致。
- README / `.env.example` / 交接文档同步；无未提交素材；提交信息合规（无 AI 共同作者）。
