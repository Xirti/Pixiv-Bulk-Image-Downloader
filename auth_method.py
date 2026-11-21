"""The user-selected Pixiv login method; credentials remain in the native vault."""
import json
from pathlib import Path
import threading


class AuthMethod:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.value = "app"
        self._needs_repair = False
        self._observed = None
        self.reload()

    def _signature(self, path=None):
        try:
            info = (path if path is not None else self.path).stat()
        except FileNotFoundError:
            return None
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size

    def changed(self):
        return self._signature() != self._observed

    def reload(self):
        """Adopt the shared selection while the caller holds the session mutex."""
        with self.lock:
            observed = self._signature()
            if observed == self._observed:
                return False
            value = "app"
            if observed is not None:
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                except (ValueError, RecursionError):
                    raw = None
                if not isinstance(raw, dict) or raw.get("method") not in ("app", "web"):
                    self._observed = observed
                    self._needs_repair = True
                    return False
                value = raw["method"]
            changed = self.value != value
            self.value = value
            self._observed = observed
            self._needs_repair = False
            return changed

    def get(self):
        with self.lock:
            return self.value

    @property
    def needs_repair(self):
        with self.lock:
            return self._needs_repair

    def set(self, value):
        if value not in {"app", "web"}:
            raise ValueError("请选择 App 授权或网页登录")
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"method": value}), encoding="utf-8")
            observed = self._signature(temporary)
            temporary.replace(self.path)
            self.value = value
            self._observed = observed
            self._needs_repair = False
