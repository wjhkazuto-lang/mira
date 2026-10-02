def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：每个字符算 1 个（中文偏保守的上限）。"""
    return len(text)


def clip_text(text: str, limit: int) -> str:
    """超过 limit 字的文本截短，并注明省略了多少字。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}……（省略 {len(text) - limit} 字）"


def bigrams(text: str) -> set[str]:
    """把文本切成"连续字母数字段"，在每段内取相邻两字；单字段取该字本身。"""
    out: set[str] = set()
    run: list[str] = []
    for ch in text.lower() + " ":
        if ch.isalnum():
            run.append(ch)
            continue
        if len(run) == 1:
            out.add(run[0])
        out.update(run[i] + run[i + 1] for i in range(len(run) - 1))
        run = []
    return out
