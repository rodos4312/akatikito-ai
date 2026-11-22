"""Reentrant file locks shared by Desktop and the detached gateway."""
from __future__ import annotations
from contextlib import contextmanager
import os, tempfile, threading, time
from pathlib import Path

_LOCK_DIR_CACHE: dict[tuple[str, str], Path] = {}
_LOCK_DIR_GUARD = threading.Lock()


def writable_dir(*candidates: Path) -> Path:
    """Return the first candidate directory verified writable via a quick probe file."""
    last_exc: OSError | None = None
    for cand in candidates:
        probe = cand / f".probe-{os.getpid()}-{threading.get_ident()}"
        try:
            cand.mkdir(parents=True, exist_ok=True)
            with probe.open("wb") as stream:
                stream.write(b"1")
            probe.unlink(missing_ok=True)
            return cand
        except OSError as exc:
            last_exc = exc
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
    if last_exc is not None:
        raise last_exc
    return Path(tempfile.gettempdir())


def _lock_dir() -> Path:
    override = os.environ.get('AKATIKITO_LOCK_DIR', '').strip()
    temp_root = tempfile.gettempdir()
    cache_key = (override, temp_root)
    with _LOCK_DIR_GUARD:
        cached = _LOCK_DIR_CACHE.get(cache_key)
        if cached is not None:
            return cached
        candidates: list[Path] = []
        if override:
            candidates.append(Path(override))
        if Path('D:/').exists():
            candidates.append(Path('D:/Meu-Temp/akatikito-locks'))
        candidates.append(Path(temp_root) / 'akatikito-locks')
        chosen = writable_dir(*candidates)
        _LOCK_DIR_CACHE[cache_key] = chosen
        return chosen


class FileLock:
    def __init__(self,path,timeout=30):
        import hashlib
        identity=str(Path(path).resolve()).casefold()
        self.path=_lock_dir()/(hashlib.sha256(identity.encode()).hexdigest()+'.lock')
        self.timeout=timeout;self.thread=threading.RLock();self.local=threading.local()
    def __enter__(self):
        self.thread.acquire()
        try:
            if not getattr(self.local,'depth',0):
                self.path.parent.mkdir(parents=True,exist_ok=True)
                stream=self.path.open('a+b');stream.seek(0)
                if self.path.stat().st_size==0:stream.write(b'0');stream.flush()
                deadline=time.monotonic()+self.timeout
                while True:
                    try:
                        stream.seek(0)
                        if os.name=='nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                        break
                    except (OSError,BlockingIOError):
                        if time.monotonic()>=deadline:
                            stream.close();raise TimeoutError('Η κοινή εργασία χρησιμοποιείται από άλλη διεργασία.')
                        time.sleep(.025)
                self.local.stream=stream
            self.local.depth=getattr(self.local,'depth',0)+1
            return self
        except BaseException:self.thread.release();raise
    def __exit__(self,*exc):
        self.local.depth-=1
        if not self.local.depth:
            stream=self.local.stream;stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            stream.close();del self.local.stream
        self.thread.release()

@contextmanager
def project_lock(root):
    from .settings import DEFAULT_PATH
    import hashlib
    key=hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()
    with FileLock(DEFAULT_PATH.parent/'locks'/('project-'+key+'.lock')):yield
