"""统计 Mira 的说话风格，用来判断"像不像机器人"有没有改善。只读数据库，不调用 API，不花钱。

用法：
    uv run python evals/style_stats.py                      # 全部聊天记录
    uv run python evals/style_stats.py --since 2026-10-03   # 只看某天之后的
"""

import argparse
import sqlite3
import sys
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _questions(text: str) -> int:
    return text.count("？") + text.count("?")


def style_stats(rows: list[tuple[str, str | None, str]]) -> dict:
    """rows: 按时间排序的 (role, batch_id, content)。同一批的 Mira 气泡算作一轮回复。"""
    turns: OrderedDict = OrderedDict()
    for role, batch_id, content in rows:
        if role == "assistant":
            turns.setdefault(batch_id, []).append(content)
    replies = list(turns.values())
    return {
        "turns": len(replies),
        "bubbles": dict(sorted(Counter(len(r) for r in replies).items())),
        "ends_with_question": sum(1 for r in replies if r[-1].rstrip().endswith(("？", "?"))),
        "multi_question": sum(1 for r in replies if sum(_questions(b) for b in r) >= 2),
        "shuo_shuo_kan": sum(b.count("说说看") for r in replies for b in r),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="只统计这个日期（YYYY-MM-DD）之后的聊天")
    parser.add_argument("--db", default=str(ROOT / "data" / "mira.db"))
    args = parser.parse_args()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    sql, params = "SELECT role, batch_id, content FROM messages", []
    if args.since:
        sql += " WHERE created_at >= ?"
        params.append(args.since)
    s = style_stats(db.execute(sql + " ORDER BY id", params).fetchall())
    if not s["turns"]:
        print("这段时间还没有 Mira 的回复")
        sys.exit(0)
    n = s["turns"]
    print(f"回复轮数：{n}")
    print(f"每轮气泡数分布：{s['bubbles']}（越分散越自然）")
    print(f"以问句结尾：{s['ends_with_question']}/{n}（{s['ends_with_question'] / n:.0%}）")
    print(f"一轮问了两个以上问题：{s['multi_question']}/{n}（{s['multi_question'] / n:.0%}）")
    print(f"“说说看”出现次数：{s['shuo_shuo_kan']}")


if __name__ == "__main__":
    main()
