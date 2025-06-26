import os
import json
import threading
import logging

logger = logging.getLogger(__name__)

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
                logger.error(f"[LocalTagManager] 加载本地tag失败: {e}")
                return {}
        return {}

    def save(self):
        # 不要再加锁，避免死锁
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.tags, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[LocalTagManager] 保存本地tag失败: {e}")

    def replace(self, text: str) -> tuple[str, list[str]]:
        original_text = text
        changed_tags = []
        tags = sorted(self.tags.items(), key=lambda x: -len(x[0]))
        for k, v in tags:
            if k in text: # 检查原始文本中是否存在该关键词
                text = text.replace(k, v)
                if k not in changed_tags: # 避免重复添加
                    changed_tags.append(k)
        return text, changed_tags

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
