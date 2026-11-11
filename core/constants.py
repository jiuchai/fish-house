"""插件常量：插件名、内置素材与默认设置。"""

from __future__ import annotations

from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_fish_house"
VERSION = "3.0.0"

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
"""插件根目录（core/ 的上一级）。"""

ALLOWED_EXT = {".mp4", ".webm", ".m4v", ".mov"}
EXPORT_NAME = "astrbot_plugin_fish_house.zip"

DEFAULT_SETTINGS = {
    "crossfade_ms": 400,
    "bubble_enabled": 1,
    "defaults_applied": 0,
}
