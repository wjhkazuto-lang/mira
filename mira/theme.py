"""本地主题素材（立绘、背景）的发现与表情选择。

素材放在项目的 theme/ 文件夹里（不提交 git）：
    theme/mira/<表情>.png         比如 calm.png、happy.png
    theme/background/<时段>.jpg   day / dusk / night，任选几张；也可以是 .gif / .mp4 / .webm
没有素材时一切照旧，界面保持朴素样式。
"""

from pathlib import Path

# 表情名 → 给模型看的中文说明。顺序就是展示和兜底的优先顺序。
EXPRESSIONS = {
    "calm": "平静",
    "talk": "正常说话",
    "smile": "闭眼微笑、轻松",
    "happy": "开心地笑",
    "gentle": "温柔、安慰",
    "worried": "担心、难过",
    "surprised": "惊讶",
    "annoyed": "皱眉、不满（吐槽时）",
}
# Mira 没选表情或选的表情没有图时，按回复方式兜底
APPROACH_DEFAULTS = {"comfort": "gentle", "crisis": "worried", "raise_issue": "calm", "normal": "calm"}
BACKGROUND_SLOTS = ("day", "dusk", "night")
IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")
# 背景还可以是动图或视频（视频在页面里静音循环播放）
BACKGROUND_SUFFIXES = IMAGE_SUFFIXES + (".gif", ".mp4", ".webm")


class Theme:
    def __init__(self, root: Path):
        self.root = root

    def _find(self, folder: str, name: str, suffixes: tuple[str, ...] = IMAGE_SUFFIXES) -> Path | None:
        for suffix in suffixes:
            path = self.root / folder / f"{name}{suffix}"
            if path.is_file():
                return path
        return None

    def expressions(self) -> list[str]:
        return [name for name in EXPRESSIONS if self._find("mira", name)]

    def urls(self) -> dict[str, str]:
        return {name: f"/theme/mira/{self._find('mira', name).name}" for name in self.expressions()}

    def avatar_url(self) -> str | None:
        """窄屏气泡旁的小头像（theme/mira/avatar.png，裁好的脸部）；没有就用立绘本身。"""
        path = self._find("mira", "avatar")
        return f"/theme/mira/{path.name}" if path else None

    def backgrounds(self) -> dict[str, str]:
        out = {}
        for slot in BACKGROUND_SLOTS:
            path = self._find("background", slot, BACKGROUND_SUFFIXES)
            if path:
                out[slot] = f"/theme/background/{path.name}"
        return out

    def pick(self, expression: str | None, approach: str) -> str | None:
        available = self.expressions()
        if not available:
            return None
        for candidate in (expression, APPROACH_DEFAULTS.get(approach), "calm"):
            if candidate in available:
                return candidate
        return available[0]

    def prompt_hint(self) -> str:
        """写进聊天规则里的一段话：有立绘时让 Mira 选表情，没有时不提。"""
        available = self.expressions()
        if not available:
            return ""
        options = "、".join(f"{name}（{EXPRESSIONS[name]}）" for name in available)
        return (
            "你在界面上有立绘。请在 json 里加一个 expression 字段，从这些表情里选一个最贴合你这次回复语气的："
            f"{options}。不要每次都选同一个。"
        )
