"""生成 macOS 的 Mira.app 启动壳：uv run python -m mira.make_app"""

import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mira.config import PROJECT_ROOT

BUNDLE_ID = "local.mira.desktop"
_ICON_SIZES = (16, 32, 128, 256, 512)


def launcher_script(project_root: Path, uv_path: Path) -> str:
    return f"#!/bin/zsh\ncd {shlex.quote(str(project_root))} && exec {shlex.quote(str(uv_path))} run python -m mira.desktop\n"


def info_plist() -> bytes:
    return plistlib.dumps({
        "CFBundleName": "Mira", "CFBundleDisplayName": "Mira",
        "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": "Mira",
        "CFBundlePackageType": "APPL", "CFBundleIconFile": "Mira",
        "NSHighResolutionCapable": True,
    })


def make_icns(png: Path, out: Path) -> bool:
    """用 sips + iconutil 把 png 变成 icns；任何一步失败都返回 False。"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "Mira.iconset"
            iconset.mkdir()
            for s in _ICON_SIZES:
                for scale in (1, 2):
                    name = f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png"
                    px = str(s * scale)
                    subprocess.run(["sips", "-z", px, px, str(png), "--out", str(iconset / name)],
                                   check=True, capture_output=True)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                           check=True, capture_output=True)
        return out.exists() and out.stat().st_size > 0
    except Exception:
        return False


def _is_our_app(dest: Path) -> bool:
    try:
        plist = plistlib.loads((dest / "Contents/Info.plist").read_bytes())
        return plist.get("CFBundleIdentifier") == BUNDLE_ID
    except Exception:
        return False


def build_app(dest: Path, project_root: Path, uv_path: Path, icon_png: Path | None) -> Path:
    if dest.exists() or dest.is_symlink():
        if not _is_our_app(dest):
            raise FileExistsError(f"{dest} 已经存在，而且不是 Mira 生成的，没有覆盖。请换个位置或先手动处理。")
        shutil.rmtree(dest)
    macos, resources = dest / "Contents/MacOS", dest / "Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    (dest / "Contents/Info.plist").write_bytes(info_plist())
    exe = macos / "Mira"
    exe.write_text(launcher_script(project_root, uv_path))
    exe.chmod(0o755)
    if icon_png is not None:
        make_icns(icon_png, resources / "Mira.icns")  # 图标失败不影响 app 本身
    dest.touch()  # 让 Finder 刷新图标
    return dest


def main() -> None:
    found = shutil.which("uv")
    uv = Path(found) if found else Path.home() / ".local/bin/uv"
    if not uv.exists():
        print("找不到 uv，请先安装 uv（或确认它在 ~/.local/bin/uv）。")
        sys.exit(1)
    apps = Path.home() / "Applications"
    apps.mkdir(exist_ok=True)
    avatar = PROJECT_ROOT / "theme/mira/avatar.png"
    try:
        dest = build_app(apps / "Mira.app", PROJECT_ROOT, uv.absolute(),
                         avatar if avatar.exists() else None)
    except FileExistsError as e:
        print(e)
        sys.exit(1)
    print(f"已生成 {dest}，可以拖进 Dock")


if __name__ == "__main__":
    main()
