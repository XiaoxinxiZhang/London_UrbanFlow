"""A process lock shared by the scheduled collector and the local web server."""
from pathlib import Path
import os


class CollectorLock:
    def __init__(self, database):
        self.path = Path(str(Path(database).resolve()) + ".collector.lock")
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def collector_running(database):
    lock = CollectorLock(database)
    if not lock.acquire():
        return True
    lock.release()
    return False
