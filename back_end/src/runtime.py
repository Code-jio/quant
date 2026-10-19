"""Single executor ownership and consistent SQLite recovery utilities."""

from pathlib import Path
import os
import sys
import sqlite3


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                if self.path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("Another trading executor owns this workspace") from exc
        self.file = handle

    def release(self):
        if self.file:
            self.file.close()
            self.file = None


def backup_database(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_file() or destination.exists() or source == destination:
        raise ValueError("Source must exist and backup destination must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup failed SQLite integrity check")


def restore_database(source, destination):
    """Restore into a new path; never overwrite a running executor's database."""
    backup_database(source, destination)
