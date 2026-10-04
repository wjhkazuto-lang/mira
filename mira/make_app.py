"""生成 macOS 的 Mira.app 启动壳：uv run python -m mira.make_app"""

import errno
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mira.config import PROJECT_ROOT
from mira.icon import rounded_icon_png

BUNDLE_ID = "local.mira.desktop"
_ICON_SIZES = (16, 32, 128, 256, 512)


def _alert(title: str, message: str) -> str:
    return "osascript -e " + shlex.quote(f'display alert "{title}" message "{message}"')


def launcher_script(project_root: Path, uv_path: Path) -> str:
    """不用 exec：出错时还能弹个提示，免得 Dock 图标跳一下就没了。osascript 用名字调用，测试里可以换成假的。"""
    moved = _alert("Mira 打不开", "找不到项目文件夹，请在新位置重新运行 uv run python -m mira.make_app")
    crashed = _alert("Mira 意外退出", "请查看项目里的 data/logs/mira.log")
    return (
        "#!/bin/zsh\n"
        f"cd {shlex.quote(str(project_root))} || {{ {moved}; exit 1; }}\n"
        f"{shlex.quote(str(uv_path))} run python -m mira.desktop || {{ {crashed}; exit 1; }}\n"
    )


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
            rounded = Path(tmp) / "rounded.png"
            if rounded_icon_png(png, rounded):  # 圆角留白；失败就用原图
                png = rounded
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
        out.unlink(missing_ok=True)  # 别留下写了一半的图标
        return False


def _is_our_app(dest: Path) -> bool:
    try:
        plist = plistlib.loads((dest / "Contents/Info.plist").read_bytes())
        return plist.get("CFBundleIdentifier") == BUNDLE_ID
    except Exception:
        return False


def build_app(dest: Path, project_root: Path, uv_path: Path, icon_png: Path | None) -> Path:
    if dest.is_symlink():
        raise FileExistsError("目标位置是一个快捷方式（符号链接），为安全起见不会覆盖。请把它删掉后重新运行。")
    if dest.exists():
        if not _is_our_app(dest):
            raise FileExistsError(f"{dest} 已经存在，而且不是 Mira 生成的，没有覆盖。请把它改名或移到废纸篓后重新运行。")
        shutil.rmtree(dest)
    macos, resources = dest / "Contents/MacOS", dest / "Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    (dest / "Contents/Info.plist").write_bytes(info_plist())
    exe = macos / "Mira"
    exe.write_text(launcher_script(project_root, uv_path), encoding="utf-8")
    exe.chmod(0o755)
    if icon_png is not None:
        if not make_icns(icon_png, resources / "Mira.icns"):  # 图标失败不影响 app 本身
            print("图标没有生成成功，App 会使用默认图标。")
    dest.touch()  # 让 Finder 刷新图标
    return dest


def _os_error_text(e: OSError) -> str:
    if e.errno == errno.ENOSPC:
        return "磁盘空间不足"
    if e.errno in (errno.EACCES, errno.EPERM):
        return "没有权限"
    return str(e)


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
    except OSError as e:
        print(f"生成 Mira.app 失败：{_os_error_text(e)}")
        sys.exit(1)
    print(f"已生成 {dest}，可以拖进 Dock")
    try:
        subprocess.run(["open", "-R", str(dest)], check=False)  # 在 Finder 里把它指给你看
    except OSError:
        pass


if __name__ == "__main__":
    main()
