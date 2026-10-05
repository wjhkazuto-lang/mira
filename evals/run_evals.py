"""质量评估：用虚构对话跑真实的记忆写入器/反思器，也跑主动消息/每周信场景，检查表现对不对。

会调用真实的 DeepSeek API（跑一遍约几分钱）。用法：
    uv run python evals/run_evals.py          # 运行前会让你确认
    uv run python evals/run_evals.py --yes    # 跳过确认
"""

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mira.store import Store  # noqa: E402

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
PROACTIVE_DIR = Path(__file__).resolve().parent / "scenarios_proactive"

async def _instant(_seconds: float) -> None:
    await asyncio.sleep(0)  # 评测里气泡不用真的等


def load_scenarios() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SCENARIO_DIR.glob("*.json"))]


def seed(store: Store, memories: list[dict], vector_for) -> list[int]:
    """预置记忆。vector_for 是一个向量，或一个 text -> 向量 的函数。"""
    ids = []
    for m in memories:
        vec = vector_for(m["content"]) if callable(vector_for) else vector_for
        ids.append(
            store.add_memory(
                m["type"],
                m["content"],
                vector=vec,
                actor="seed",
                subject=m.get("subject"),
                status=m.get("status"),
                due_at=date.fromisoformat(m["due_at"]) if m.get("due_at") else None,
                evidence=m.get("evidence"),
            ).id
        )
    return ids


def check(store: Store, expectations: list[dict]) -> list[str]:
    """返回不满足的期望（中文描述）；全部满足时返回空列表。"""
    failures = []
    for exp in expectations:
        t = exp["type"]
        if "superseded_min" in exp:
            n = sum(1 for m in store.list_memories(t) if m.superseded_by is not None)
            if n < exp["superseded_min"]:
                failures.append(f"{t}：被替代的记忆 {n} 条，期望至少 {exp['superseded_min']} 条")
            continue
        items = store.list_memories(t, include_superseded=False)
        if "contains" in exp:
            items = [m for m in items if exp["contains"] in m.content]
        for key in ("due_at", "status", "subject"):
            if key in exp:
                items = [m for m in items if str(getattr(m, key) or "") == exp[key]]
        desc = f"{t}" + "".join(f" {k}={exp[k]}" for k in ("contains", "due_at", "status", "subject") if k in exp)
        has_filter = any(k in exp for k in ("contains", "due_at", "status", "subject"))
        min_count = exp.get("min_count", 1 if has_filter and "max_count" not in exp else 0)
        if len(items) < min_count:
            failures.append(f"{desc}：找到 {len(items)} 条，期望至少 {min_count} 条")
        if "max_count" in exp and len(items) > exp["max_count"]:
            failures.append(f"{desc}：找到 {len(items)} 条，期望最多 {exp['max_count']} 条")
    return failures


async def run_scenario(scenario: dict, llm, embedder, settings) -> list[str]:
    from mira.reflector import Reflector
    from mira.retriever import Retriever
    from mira.writer import Writer

    now = datetime.fromisoformat(scenario["now"])
    store = Store(":memory:", now=lambda: now)
    seed(store, scenario.get("seed_memories", []), lambda text: embedder.embed([text])[0])
    for m in scenario["messages"]:
        store.add_message(m["role"], m["content"])
    for step in scenario["run"]:
        if step == "writer":
            retriever = Retriever(store, embedder, now=lambda: now)
            await Writer(store, embedder, retriever, llm, settings, now=lambda: now).run()
        else:
            await Reflector(store, embedder, llm, settings, now=lambda: now).run()
    return check(store, scenario["expect"])


class _Clock:
    """可改的假时钟：给内存数据库定"现在"。"""

    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class _CountingLLM:
    """包一层真 LLM，数"这一轮问了几次模型"（沉默场景要求零次）。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    async def complete_json(self, **kw):
        self.calls += 1
        return await self.inner.complete_json(**kw)


def load_proactive_scenarios() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(PROACTIVE_DIR.glob("*.json"))]


async def run_proactive_scenario(scenario: dict, llm, embedder, settings) -> list[str]:
    """跑一个主动消息/每周信场景，返回不满足的期望（中文）；全满足返回空列表。"""
    from mira.chat import ChatEngine
    from mira.prompts import render
    from mira.proactive import ProactiveEngine
    from mira.retriever import Retriever

    now = datetime.fromisoformat(scenario["now"])
    clock = _Clock(now)
    store = Store(":memory:", now=clock)
    for m in scenario.get("seed_memories", []):
        clock.t = now - timedelta(minutes=m.get("ago_minutes", 0))
        store.add_memory(
            m["type"], m["content"], vector=embedder.embed([m["content"]])[0], actor="seed",
            status=m.get("status"), importance=m.get("importance", 3),
            due_at=date.fromisoformat(m["due_at"]) if m.get("due_at") else None,
        )
    seeded_ids = set()
    for m in scenario.get("messages", []):
        clock.t = now - timedelta(minutes=m.get("ago_minutes", 0))
        seeded_ids.add(store.add_message(m["role"], m["content"]).id)
    for name, iso in scenario.get("job_last_run", {}).items():
        store.set_job_last_run(name, datetime.fromisoformat(iso))
    clock.t = now

    counting = _CountingLLM(llm)
    retriever = Retriever(store, embedder, now=clock)
    persona = settings.persona_path.read_text(encoding="utf-8")
    rules = render("chat_rules", crisis_resources=settings.crisis_resources, expression_hint="")
    chat = ChatEngine(store=store, retriever=retriever, llm=counting, settings=settings,
                      persona=persona, rules=rules, now=clock, sleep=_instant)
    proactive = ProactiveEngine(store=store, llm=counting, retriever=retriever, settings=settings,
                                engine=chat, persona=persona, rules=rules, now=clock)

    expect = scenario["expect"]
    if scenario["run"] == "weekly":
        if proactive.weekly_ready():
            await proactive.run_weekly()
    elif proactive.ready():  # 和调度器一样：闸门不过就什么都不做
        await proactive.run()

    spoken = [m for m in store.list_messages() if m.role == "assistant" and m.id not in seeded_ids]
    failures: list[str] = []
    if expect.get("speak") is True and not spoken:
        failures.append("期望开口，但什么都没说")
    if expect.get("speak") is False and spoken:
        failures.append(f"期望沉默，但说了：{spoken[0].content[:40]}")
    if "calls" in expect and counting.calls != expect["calls"]:
        failures.append(f"模型调用了 {counting.calls} 次，期望 {expect['calls']} 次")
    if "kind" in expect and spoken:
        got = spoken[0].meta.get("proactive", {}).get("kind")
        if got != expect["kind"]:
            failures.append(f"开口原因 kind={got}，期望 {expect['kind']}")
    if "contains" in expect and spoken:
        if not any(expect["contains"] in m.content for m in spoken):
            failures.append(f"没有一条提到“{expect['contains']}”：{[m.content[:30] for m in spoken]}")
    if "bubbles_min" in expect and len(spoken) < expect["bubbles_min"]:
        failures.append(f"说了 {len(spoken)} 条，期望至少 {expect['bubbles_min']} 条")
    if "bubbles_max" in expect and len(spoken) > expect["bubbles_max"]:
        failures.append(f"说了 {len(spoken)} 条，期望最多 {expect['bubbles_max']} 条")
    return failures


async def main() -> int:
    from mira.config import load_settings
    from mira.embedder import FastEmbedder
    from mira.llm import DeepSeekLLM

    scenarios = load_scenarios()
    proactive_scenarios = load_proactive_scenarios()
    total = len(scenarios) + len(proactive_scenarios)
    if "--yes" not in sys.argv:
        answer = input(f"将用真实的 DeepSeek API 跑 {total} 个场景（约几分钱）。继续吗？[y/N] ")
        if answer.strip().lower() != "y":
            print("已取消")
            return 1
    settings = load_settings()
    llm = DeepSeekLLM(settings.deepseek_api_key, settings.deepseek_base_url)
    embedder = FastEmbedder()

    passed = 0
    for s in scenarios:
        try:
            failures = await run_scenario(s, llm, embedder, settings)
        except Exception as e:  # 单个场景出错不影响其他场景
            failures = [f"运行出错：{e}"]
        if failures:
            print(f"✗ {s['name']}")
            for f in failures:
                print(f"    - {f}")
        else:
            passed += 1
            print(f"✓ {s['name']}")
    for s in proactive_scenarios:
        try:
            failures = await run_proactive_scenario(s, llm, embedder, settings)
        except Exception as e:
            failures = [f"运行出错：{e}"]
        if failures:
            print(f"✗ [主动] {s['name']}")
            for f in failures:
                print(f"    - {f}")
        else:
            passed += 1
            print(f"✓ [主动] {s['name']}")
    print(f"\n通过 {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
