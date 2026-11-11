"""插件配置读取与权限判断。"""

from __future__ import annotations

import re
from typing import Any

from astrbot.api.event import AstrMessageEvent



class ConfigMixin:
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default) if self.config is not None else default
        except Exception:
            value = default
        return default if value is None else value

    def _can_feedback(self, event: AstrMessageEvent) -> bool:
        if not bool(self._cfg("admin_only", False)):
            return True
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    def public_prefix_enabled(self) -> bool:
        return bool(str(self._cfg("enabled", True)).lower() not in {"0", "false", "off", "no"})

    def _prefix(self) -> str:
        prefix = str(self._cfg("public_prefix", "fish_house") or "fish_house").strip("/")
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix) or "fish_house"
