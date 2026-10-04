import os
import sqlite3
from datetime import datetime

import pytest

from mira.backup import BackupError, backup_database, prune_backups, run_backup


def make_db(path):  # WAL 模式，留一条未 checkpoint 的写入
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t(x)")
    db.execute("INSERT INTO t VALUES ('你好')")
    db.commit()
    return db  # 保持打开，-wal 文件里还有数据


NOW = datetime.fromisoformat("2026-10-04T15:30:00+08:00")
SRC_MSG = "备份失败：数据库无法读取"
DST_MSG = "备份失败：无法写入备份文件夹，请检查磁盘空间和 BACKUP_DIR"


def test_backup_is_complete_copy_while_db_open(tmp_path):
    src = make_db(tmp_path / "mira.db")
    out = backup_database(tmp_path / "mira.db", tmp_path / "backups", NOW)
    assert out == tmp_path / "backups" / "mira-20261004-153000.db"
    chk = sqlite3.connect(out)
    assert chk.execute("SELECT x FROM t").fetchall() == [("你好",)]
    chk.close()  # 读取会让 WAL 备份临时生成 -wal/-shm，关掉再检查残留
    assert [p.name for p in (tmp_path / "backups").iterdir()] == [out.name]  # 没有残留临时文件
    src.close()


def test_creates_missing_backup_dir(tmp_path):
    make_db(tmp_path / "mira.db")
    target = tmp_path / "a" / "b" / "c"
    assert backup_database(tmp_path / "mira.db", target, NOW).parent == target
    assert target.is_dir()


def test_path_with_spaces_and_chinese(tmp_path):
    d = tmp_path / "我的 项目"
    d.mkdir()
    make_db(d / "mira.db")
    assert backup_database(d / "mira.db", d / "备份", NOW).exists()


def test_same_second_returns_existing_without_overwrite(tmp_path):
    make_db(tmp_path / "mira.db")
    first = backup_database(tmp_path / "mira.db", tmp_path / "b", NOW)
    mtime = first.stat().st_mtime_ns
    assert backup_database(tmp_path / "mira.db", tmp_path / "b", NOW) == first
    assert first.stat().st_mtime_ns == mtime


def test_missing_source_raises_backup_error(tmp_path):
    with pytest.raises(BackupError) as e:
        backup_database(tmp_path / "nope.db", tmp_path / "b", NOW)
    assert e.value.user_message == SRC_MSG
    assert not (tmp_path / "nope.db").exists()  # 不能因为只读连接而"创建"出空库


def test_unwritable_dir_raises_and_leaves_nothing(tmp_path):
    make_db(tmp_path / "mira.db")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("当前用户不受目录权限限制（如 root）")
        with pytest.raises(BackupError) as e:
            backup_database(tmp_path / "mira.db", ro, NOW)
        assert e.value.user_message == DST_MSG  # 是备份文件夹的问题，不是聊天数据库坏了
    finally:
        ro.chmod(0o700)
    assert list(ro.iterdir()) == []


def test_unreadable_source_gets_source_message(tmp_path):
    make_db(tmp_path / "mira.db").close()
    (tmp_path / "mira.db").chmod(0o000)
    try:
        if os.access(tmp_path / "mira.db", os.R_OK):
            pytest.skip("当前用户不受文件权限限制（如 root）")
        with pytest.raises(BackupError) as e:
            backup_database(tmp_path / "mira.db", tmp_path / "b", NOW)
        assert e.value.user_message == SRC_MSG
    finally:
        (tmp_path / "mira.db").chmod(0o600)
    assert list((tmp_path / "b").iterdir()) == []


def test_disk_full_during_copy_gets_destination_message(tmp_path, monkeypatch):
    make_db(tmp_path / "mira.db")
    real_connect = sqlite3.connect
    full = sqlite3.OperationalError("database or disk is full")
    full.sqlite_errorname = "SQLITE_FULL"

    class Src:  # 源库照常打开，复制时报"磁盘满了"
        def __init__(self, conn):
            self.conn = conn

        def backup(self, dst):
            raise full

        def close(self):
            self.conn.close()

    def connect(path, *a, **kw):
        conn = real_connect(path, *a, **kw)
        return Src(conn) if kw.get("uri") else conn

    monkeypatch.setattr("mira.backup.sqlite3.connect", connect)
    with pytest.raises(BackupError) as e:
        backup_database(tmp_path / "mira.db", tmp_path / "b", NOW)
    assert e.value.user_message == DST_MSG
    assert list((tmp_path / "b").iterdir()) == []


def test_prune_keeps_newest_and_ignores_other_files(tmp_path):
    for i in range(1, 17):
        (tmp_path / f"mira-202610{i:02d}-120000.db").touch()
    for name in ("dev-20261001-120000.db", "mira-手动.db", "notes.txt"):
        (tmp_path / name).touch()
    removed = prune_backups(tmp_path, "mira", 14)
    assert sorted(p.name for p in removed) == ["mira-20261001-120000.db", "mira-20261002-120000.db"]
    left = {p.name for p in tmp_path.iterdir()}
    assert {"dev-20261001-120000.db", "mira-手动.db", "notes.txt"} <= left and len(left) == 17


def test_run_backup_prunes_with_db_stem_prefix(tmp_path):
    make_db(tmp_path / "dev.db")
    b = tmp_path / "b"
    b.mkdir()
    for i in (1, 2, 3):
        (b / f"dev-2026090{i}-120000.db").touch()
        (b / f"mira-2026090{i}-120000.db").touch()
    out = run_backup(tmp_path / "dev.db", b, 2, NOW)
    assert out.name == "dev-20261004-153000.db"
    names = {p.name for p in b.iterdir()}
    assert {n for n in names if n.startswith("dev-")} == {"dev-20261004-153000.db", "dev-20260903-120000.db"}
    assert len([n for n in names if n.startswith("mira-")]) == 3


def test_run_backup_prune_failure_raises_backup_error_and_keeps_new_backup(tmp_path, monkeypatch):
    make_db(tmp_path / "mira.db")

    def boom(*a):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("mira.backup.prune_backups", boom)
    with pytest.raises(BackupError) as e:
        run_backup(tmp_path / "mira.db", tmp_path / "b", 1, NOW)
    assert e.value.user_message.startswith("备份失败")
    assert (tmp_path / "b" / "mira-20261004-153000.db").exists()
