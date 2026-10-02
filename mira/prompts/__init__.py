from pathlib import Path

_DIR = Path(__file__).parent


def render(name: str, **vars: str) -> str:
    """读取 prompts/{name}.md，把 {{key}} 替换为对应的值；有没替换掉的占位符就报 KeyError。"""
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in vars.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        start = text.index("{{")
        raise KeyError(f"提示词 {name} 缺少变量：{text[start:text.index('}}', start) + 2]}")
    return text
