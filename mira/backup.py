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


def _write_side(e: sqlite3.Error) -> bool:
    """复制过程中的错：磁盘满、只读、打不开、写入出错，算备份文件夹那边的问题。"""
    name = getattr(e, "sqlite_errorname", "") or ""
    if name.startswith("SQLITE_IOERR"):
        return "READ" not in name
    return name.startswith(("SQLITE_FULL", "SQLITE_READONLY", "SQLITE_CANTOPEN"))


def backup_database(db_path: Path, backup_dir: Path, now: datetime) -> Path:
    final = backup_dir / f"{db_path.stem}-{now:%Y%m%d-%H%M%S}.db"
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
                ok = dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            except sqlite3.Error as e:
                raise BackupError(DST_FAILED if _write_side(e) else SRC_FAILED) from e
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
        raise BackupError(f"备份失败：{e.strerror or e}") from e
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


def run_backup(db_path: Path, backup_dir: Path, keep: int, now: datetime) -> Path:
    out = backup_database(db_path, backup_dir, now)
    try:
        prune_backups(backup_dir, db_path.stem, keep)
    except OSError as e:  # 新备份已成功并保留，但清理失败要让页面看到
        raise BackupError(f"备份失败：{e.strerror or e}") from e
    return out
