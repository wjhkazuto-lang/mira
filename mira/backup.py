import errno
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path


class BackupError(Exception):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message  # 中文，可直接给页面


SRC_FAILED = "备份失败：数据库无法读取"
DST_FAILED = "备份失败：无法写入备份文件夹，请检查磁盘空间和 BACKUP_DIR"


MANUAL_KEEP = 5  # 手动备份单独计数，最多留 5 份

_ERRNO_TEXT = {
    errno.ENOSPC: "磁盘空间不足",
    errno.EACCES: "没有权限写入备份文件夹",
    errno.EPERM: "没有权限写入备份文件夹",
    errno.ENOENT: "找不到文件或文件夹",
    errno.EROFS: "备份位置是只读的",
}


def _os_message(e: OSError) -> str:
    text = _ERRNO_TEXT.get(e.errno) or f"系统错误（{errno.errorcode.get(e.errno, '未知') if e.errno else '未知'}）"
    return f"备份失败：{text}"


def _source_readable(db_path: Path) -> bool:
    """用新的只读连接试读一下源库，用来判断复制出错到底是哪一边的问题。"""
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        finally:
            conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def _quick_check_ok(conn) -> bool:
    return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def _write_side(e: sqlite3.Error) -> bool:
    """复制过程中的错：磁盘满、只读、打不开、写入出错，算备份文件夹那边的问题。"""
    name = getattr(e, "sqlite_errorname", "") or ""
    if name.startswith("SQLITE_IOERR"):
        return "READ" not in name
    return name.startswith(("SQLITE_FULL", "SQLITE_READONLY", "SQLITE_CANTOPEN"))


def backup_database(db_path: Path, backup_dir: Path, now: datetime, *, manual: bool = False) -> Path:
    prefix = f"{db_path.stem}-manual" if manual else db_path.stem
    final = backup_dir / f"{prefix}-{now:%Y%m%d-%H%M%S}.db"
    tmp = backup_dir / f".{final.name}.tmp"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if final.exists():  # 同一秒重复触发，直接用现成的
            return final
        tmp.unlink(missing_ok=True)
        # mode=ro：源文件不存在时报错，而不是新建空库
        try:
            src = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except sqlite3.Error as e:
            raise BackupError(SRC_FAILED) from e
        try:
            try:
                dst = sqlite3.connect(tmp)
            except sqlite3.Error as e:
                raise BackupError(DST_FAILED) from e
            try:
                src.backup(dst)
                ok = _quick_check_ok(dst)
            except sqlite3.Error as e:
                # 打不开、写入出错这类错两边都可能：源库还能读才怪备份文件夹
                dst_side = _write_side(e) and _source_readable(db_path)
                raise BackupError(DST_FAILED if dst_side else SRC_FAILED) from e
            finally:
                dst.close()
        finally:
            src.close()
        if not ok:
            raise BackupError("备份失败：备份文件校验未通过")
        os.replace(tmp, final)
        return final
    except BackupError:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise BackupError(_os_message(e)) from e
    except sqlite3.Error as e:
        tmp.unlink(missing_ok=True)
        raise BackupError(SRC_FAILED) from e


def prune_backups(backup_dir: Path, prefix: str, keep: int) -> list[Path]:
    pattern = re.compile(rf"^{re.escape(prefix)}-\d{{8}}-\d{{6}}\.db$")
    files = sorted((p for p in backup_dir.iterdir() if pattern.match(p.name)), key=lambda p: p.name, reverse=True)
    removed = files[keep:]
    for p in removed:
        p.unlink()
    return removed


def run_backup(db_path: Path, backup_dir: Path, keep: int, now: datetime, *, manual: bool = False) -> Path:
    out = backup_database(db_path, backup_dir, now, manual=manual)
    try:
        if manual:
            prune_backups(backup_dir, f"{db_path.stem}-manual", MANUAL_KEEP)
        else:
            prune_backups(backup_dir, db_path.stem, keep)
    except OSError as e:  # 新备份已成功并保留，但清理失败要让页面看到
        raise BackupError(_os_message(e)) from e
    return out
