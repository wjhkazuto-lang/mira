def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：每个字符算 1 个（中文偏保守的上限）。"""
    return len(text)
