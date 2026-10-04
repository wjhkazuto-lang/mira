import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path


class BackupError(Exception):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message  # 中文，可直接给页面


def backup_database(db_path: Path, backup_dir: Path, now: datetime) -> Path:
    final = backup_dir / f"{db_path.stem}-{now:%Y%m%d-%H%M%S}.db"
    tmp = backup_dir / f".{final.name}.tmp"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if final.exists():  # 同一秒重复触发，直接用现成的
            return final
        tmp.unlink(missing_ok=True)
        # mode=ro：源文件不存在时报错，而不是新建空库
        src = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
                ok = dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
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
        raise BackupError("备份失败：数据库无法读取") from e


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
