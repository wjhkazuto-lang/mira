import plistlib
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from mira.make_app import BUNDLE_ID, build_app, launcher_script, make_icns


def _png(path: Path, size: int = 64) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    row = b"\x00" + b"\xc2\xa9\x74" * size
    raw = zlib.compress(row * size)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    )


def test_build_app_structure(tmp_path):
    app = build_app(tmp_path / "Mira.app", Path("/x/我的 项目"), Path("/u/uv"), None)
    plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert plist["CFBundleIdentifier"] == BUNDLE_ID == "local.mira.desktop"
    assert plist["CFBundleExecutable"] == "Mira"
    exe = app / "Contents/MacOS/Mira"
    assert exe.stat().st_mode & 0o111
    assert "'/x/我的 项目'" in exe.read_text() and "mira.desktop" in exe.read_text()


def test_launcher_runs_under_shell_with_spaces(tmp_path):
    root = tmp_path / "my proj"
    root.mkdir()
    out = tmp_path / "out.txt"
    uv = tmp_path / "fake uv"
    uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > {out.as_posix()!r}\n')
    uv.chmod(0o755)
    app = build_app(tmp_path / "Mira.app", root, uv, None)
    subprocess.run([str(app / "Contents/MacOS/Mira")], check=True)
    lines = out.read_text().splitlines()
    assert Path(lines[0]).resolve() == root.resolve()
    assert lines[1:] == ["run", "python", "-m", "mira.desktop"]


def test_launcher_script_shape():
    s = launcher_script(Path("/a b"), Path("/u/uv"))
    assert s.startswith("#!/bin/zsh") and "cd '/a b' && exec /u/uv run python -m mira.desktop" in s


def test_rebuild_overwrites_own_app(tmp_path):
    dest = tmp_path / "Mira.app"
    build_app(dest, tmp_path, Path("/u/uv"), None)
    (dest / "Contents/stale.txt").write_text("x")
    build_app(dest, tmp_path, Path("/u/uv"), None)
    assert not (dest / "Contents/stale.txt").exists()
    assert (dest / "Contents/MacOS/Mira").exists()


def test_refuses_to_overwrite_foreign_app(tmp_path):
    foreign = tmp_path / "Mira.app/Contents"
    foreign.mkdir(parents=True)
    (foreign / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.other.app"}))
    with pytest.raises(FileExistsError):
        build_app(tmp_path / "Mira.app", tmp_path, Path("/u/uv"), None)
    assert (foreign / "Info.plist").exists()


def test_refuses_when_plist_unreadable(tmp_path):
    (tmp_path / "Mira.app").mkdir()
    (tmp_path / "Mira.app/keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        build_app(tmp_path / "Mira.app", tmp_path, Path("/u/uv"), None)
    assert (tmp_path / "Mira.app/keep.txt").exists()


def test_make_icns_returns_false_on_bad_png(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    assert make_icns(bad, tmp_path / "Mira.icns") is False


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 sips/iconutil")
def test_make_icns_from_real_png(tmp_path):
    png = tmp_path / "a.png"
    _png(png)
    out = tmp_path / "Mira.icns"
    assert make_icns(png, out) is True
    assert out.stat().st_size > 0


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 sips/iconutil")
def test_build_app_with_icon(tmp_path):
    png = tmp_path / "a.png"
    _png(png)
    app = build_app(tmp_path / "Mira.app", tmp_path, Path("/u/uv"), png)
    assert (app / "Contents/Resources/Mira.icns").stat().st_size > 0
