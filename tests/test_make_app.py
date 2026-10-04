import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from mira import make_app
from mira.make_app import BUNDLE_ID, build_app, launcher_script, make_icns
from tests.conftest import make_png


def test_build_app_structure(tmp_path):
    app = build_app(tmp_path / "Mira.app", Path("/x/我的 项目"), Path("/u/uv"), None)
    plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert plist["CFBundleIdentifier"] == BUNDLE_ID == "local.mira.desktop"
    assert plist["CFBundleExecutable"] == "Mira"
    exe = app / "Contents/MacOS/Mira"
    assert exe.stat().st_mode & 0o111
    assert "'/x/我的 项目'" in exe.read_text() and "mira.desktop" in exe.read_text()


def _launch(tmp_path, app: Path) -> tuple[subprocess.CompletedProcess, Path]:
    """运行启动壳；PATH 最前面放一个假 osascript，只记录参数，绝不弹真提示框。"""
    stub_dir = tmp_path / "stub bin"
    stub_dir.mkdir(exist_ok=True)
    alerts = tmp_path / "alerts.txt"
    osa = stub_dir / "osascript"
    osa.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> {str(alerts)!r}\n')
    osa.chmod(0o755)
    env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "ZDOTDIR": str(tmp_path)}  # 不读用户的 .zshenv
    proc = subprocess.run([str(app / "Contents/MacOS/Mira")], env=env, capture_output=True, text=True, errors="replace")
    return proc, alerts


def _fake_uv(tmp_path, out: Path, code: int = 0) -> Path:
    uv = tmp_path / "fake uv"
    uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > {str(out)!r}\nexit {code}\n')
    uv.chmod(0o755)
    return uv


def test_launcher_runs_under_shell_with_spaces(tmp_path):
    root = tmp_path / "my proj"
    root.mkdir()
    out = tmp_path / "out.txt"
    app = build_app(tmp_path / "Mira.app", root, _fake_uv(tmp_path, out), None)
    proc, alerts = _launch(tmp_path, app)
    assert proc.returncode == 0
    lines = out.read_text().splitlines()
    assert Path(lines[0]).resolve() == root.resolve()
    assert lines[1:] == ["run", "python", "-m", "mira.desktop"]
    assert not alerts.exists()  # 正常退出（关窗口 / ⌘Q）不弹提示


def test_launcher_alerts_when_project_folder_missing(tmp_path):
    out = tmp_path / "out.txt"
    app = build_app(tmp_path / "Mira.app", tmp_path / "搬走了 的项目", _fake_uv(tmp_path, out), None)
    proc, alerts = _launch(tmp_path, app)
    assert proc.returncode == 1
    assert not out.exists()  # 没有去跑 uv
    text = alerts.read_text()
    assert "Mira 打不开" in text and "uv run python -m mira.make_app" in text


def test_launcher_alerts_when_mira_crashes(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    app = build_app(tmp_path / "Mira.app", root, _fake_uv(tmp_path, tmp_path / "out.txt", code=1), None)
    proc, alerts = _launch(tmp_path, app)
    assert proc.returncode == 1
    text = alerts.read_text()
    assert "Mira 意外退出" in text and "data/logs/mira.log" in text


def test_launcher_script_shape():
    s = launcher_script(Path("/a b"), Path("/u/uv"))
    assert s.startswith("#!/bin/zsh")
    assert "cd '/a b' || {" in s and "/u/uv run python -m mira.desktop || {" in s
    assert "exec " not in s  # exec 之后就没法在失败时弹提示了


def test_launcher_written_as_utf8(tmp_path):
    """中文路径：不依赖系统默认编码。"""
    code = (
        "from pathlib import Path; from mira.make_app import build_app; "
        f"build_app(Path({str(tmp_path / 'Mira.app')!r}), Path('/x/我的 项目'), Path('/u/uv'), None)"
    )
    subprocess.run([sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning", "-c", code],
                   check=True)
    assert "我的 项目" in (tmp_path / "Mira.app/Contents/MacOS/Mira").read_bytes().decode("utf-8")


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
    with pytest.raises(FileExistsError) as e:
        build_app(tmp_path / "Mira.app", tmp_path, Path("/u/uv"), None)
    assert "改名或移到废纸篓后重新运行" in str(e.value)
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
    make_png(png)
    out = tmp_path / "Mira.icns"
    assert make_icns(png, out) is True
    assert out.stat().st_size > 0


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 sips/iconutil")
def test_build_app_with_icon(tmp_path):
    png = tmp_path / "a.png"
    make_png(png)
    app = build_app(tmp_path / "Mira.app", tmp_path, Path("/u/uv"), png)
    assert (app / "Contents/Resources/Mira.icns").stat().st_size > 0


def test_main_reveals_app_in_finder(tmp_path, monkeypatch):
    uv = tmp_path / "uv"
    uv.write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))  # ~/Applications 落在临时目录
    monkeypatch.setattr(make_app, "PROJECT_ROOT", tmp_path)  # 没有头像，不调用 sips
    monkeypatch.setattr(make_app.shutil, "which", lambda name: str(uv))
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        raise OSError("open 不可用")  # 打不开 Finder 也不影响生成

    monkeypatch.setattr(make_app.subprocess, "run", fake_run)
    make_app.main()
    dest = tmp_path / "Applications" / "Mira.app"
    assert (dest / "Contents/MacOS/Mira").exists()
    assert calls == [["open", "-R", str(dest)]]


def test_refuses_symlink_dest(tmp_path):
    real = build_app(tmp_path / "real.app", tmp_path, Path("/u/uv"), None)
    link = tmp_path / "Mira.app"
    link.symlink_to(real)  # 即使指向我们自己的 app 也不覆盖
    with pytest.raises(FileExistsError) as e:
        build_app(link, tmp_path, Path("/u/uv"), None)
    assert "符号链接" in str(e.value)
    assert link.is_symlink() and (real / "Contents/Info.plist").exists()


def _main_env(tmp_path, monkeypatch, avatar=False):
    uv = tmp_path / "uv"
    uv.write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(make_app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(make_app.shutil, "which", lambda name: str(uv))
    monkeypatch.setattr(make_app.subprocess, "run", lambda cmd, **kw: None)
    if avatar:
        (tmp_path / "theme/mira").mkdir(parents=True)
        (tmp_path / "theme/mira/avatar.png").write_bytes(b"x")


def test_main_reports_oserror_in_chinese(tmp_path, monkeypatch, capsys):
    import errno
    _main_env(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(make_app, "build_app", boom)
    with pytest.raises(SystemExit) as e:
        make_app.main()
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "生成 Mira.app 失败" in out and "磁盘空间不足" in out


def test_main_oserror_permission(tmp_path, monkeypatch, capsys):
    import errno
    _main_env(tmp_path, monkeypatch)
    monkeypatch.setattr(make_app, "build_app", lambda *a, **k: (_ for _ in ()).throw(PermissionError(errno.EACCES, "denied")))
    with pytest.raises(SystemExit):
        make_app.main()
    assert "没有权限" in capsys.readouterr().out


def test_main_notes_icon_failure(tmp_path, monkeypatch, capsys):
    _main_env(tmp_path, monkeypatch, avatar=True)
    monkeypatch.setattr(make_app, "make_icns", lambda png, out: False)
    make_app.main()
    out = capsys.readouterr().out
    assert "图标没有生成成功，App 会使用默认图标。" in out
    assert (tmp_path / "Applications/Mira.app/Contents/MacOS/Mira").exists()


def test_main_no_icon_note_without_avatar(tmp_path, monkeypatch, capsys):
    _main_env(tmp_path, monkeypatch)
    make_app.main()
    assert "图标没有生成成功" not in capsys.readouterr().out


def test_make_icns_removes_partial_output(tmp_path, monkeypatch):
    out = tmp_path / "Mira.icns"

    def fake_run(cmd, **kw):
        if cmd[0] == "iconutil":
            out.write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(make_app, "rounded_icon_png", lambda a, b: False)
    monkeypatch.setattr(make_app.subprocess, "run", fake_run)
    assert make_icns(tmp_path / "a.png", out) is False
    assert not out.exists()
