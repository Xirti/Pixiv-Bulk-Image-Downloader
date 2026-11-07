"""The user-selected Pixiv login method; credentials remain in the native vault."""
import json
from pathlib import Path
import threading


class AuthMethod:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.value = "app"
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("method"),str) or raw["method"] not in {"app", "web"}:
                raise ValueError("登录方式设置无效，请检查 auth-method.json")
            self.value = raw["method"]

    def get(self):
        with self.lock:
            return self.value

    def set(self, value):
        if value not in {"app", "web"}:
            raise ValueError("请选择 App 授权或网页登录")
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"method": value}), encoding="utf-8")
            temporary.replace(self.path)
            self.value = value
