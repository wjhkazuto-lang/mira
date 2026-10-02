import re
from pathlib import Path

_DIR = Path(__file__).parent
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(name: str, **vars: str) -> str:
    """读取 prompts/{name}.md，把模板里的 {{key}} 一次性替换为对应的值。

    只检查模板本身的占位符；替换进去的内容（比如用户聊天里的 "{{name}}"）原样保留。
    """
    template = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    missing = sorted({key for key in _PLACEHOLDER.findall(template) if key not in vars})
    if missing:
        raise KeyError(f"提示词 {name} 缺少变量：{missing}")
    return _PLACEHOLDER.sub(lambda m: vars[m.group(1)], template)
