import os
import json
import threading

class LocalTagManager:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.tags = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[LocalTagManager] 加载本地tag失败: {e}")
                return {}
        return {}

    def save(self):
        # 不要再加锁，避免死锁
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.tags, f, ensure_ascii=False, indent=2)

    def replace(self, text: str) -> str:
        tags = sorted(self.tags.items(), key=lambda x: -len(x[0]))
        for k, v in tags:
            text = text.replace(k, v)
        return text

    def set_tag(self, key, value):
        with self.lock:
            self.tags[key] = value
            self.save()

    def del_tag(self, key):
        with self.lock:
            if key in self.tags:
                del self.tags[key]
                self.save()

    def get_all(self):
        return self.tags.copy()
