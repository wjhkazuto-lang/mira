"""记忆质量评估：用虚构对话跑真实的写入器/反思器，检查记住的东西对不对。

会调用真实的 DeepSeek API（跑一遍约几分钱）。用法：
    uv run python evals/run_evals.py          # 运行前会让你确认
    uv run python evals/run_evals.py --yes    # 跳过确认
"""

import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mira.store import Store  # noqa: E402

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"


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


async def main() -> int:
    from mira.config import load_settings
    from mira.embedder import FastEmbedder
    from mira.llm import DeepSeekLLM

    scenarios = load_scenarios()
    if "--yes" not in sys.argv:
        answer = input(f"将用真实的 DeepSeek API 跑 {len(scenarios)} 个场景（约几分钱）。继续吗？[y/N] ")
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
    print(f"\n通过 {passed}/{len(scenarios)}")
    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
