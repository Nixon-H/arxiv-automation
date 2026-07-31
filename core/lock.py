import os
import sys
import time
import fcntl
import atexit
from typing import Optional


LOCK_FILE = "data/run.lock"


class FileLock:
    def __init__(self, lock_path: str = LOCK_FILE) -> None:
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self, timeout: float = 0.0) -> bool:
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        try:
            self._fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            start = time.time()
            while True:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (IOError, BlockingIOError):
                    if timeout > 0 and (time.time() - start) >= timeout:
                        return False
                    time.sleep(0.5)

            self._write_pid()
            atexit.register(self.release)
            return True

        except Exception:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            return False

    def _write_pid(self) -> None:
        if self._fd is not None:
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, str(os.getpid()).encode())
            os.truncate(self._fd, os.lseek(self._fd, 0, os.SEEK_CUR))
            os.fsync(self._fd)

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def is_locked(self) -> bool:
        if not os.path.exists(self.lock_path):
            return False
        try:
            fd = os.open(self.lock_path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except (IOError, BlockingIOError):
                return True
            finally:
                os.close(fd)
        except Exception:
            return False

    @staticmethod
    def get_locked_pid() -> Optional[int]:
        if not os.path.exists(LOCK_FILE):
            return None
        try:
            with open(LOCK_FILE, "r") as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            return None
