"""面板接口路由表；接口由插件自带的 Web 服务提供，不经过 AstrBot。"""

from __future__ import annotations

ROUTES = [
    ("overview", "api_overview", "GET", "大肥鱼的小屋概览"),
    ("state", "api_state", "GET", "壁纸当前状态"),
    ("event", "api_event", "POST", "触发壁纸动作"),
    ("settings", "api_settings", "POST", "保存壁纸设置"),
    ("upload/start", "api_upload_start", "POST", "开始上传视频"),
    ("upload/chunk", "api_upload_chunk", "POST", "上传视频分片"),
    ("upload/finish", "api_upload_finish", "POST", "完成视频上传"),
    ("sync", "api_sync", "POST", "重新同步公共页面"),
    ("backups", "api_backups", "GET", "列出备份"),
    ("restore", "api_restore", "POST", "恢复备份"),
    ("flow", "api_flow", "POST", "流程图保存/生成"),
    ("library", "api_library", "POST", "素材库管理"),
    ("theme", "api_theme", "POST", "主题导出/导入/应用"),
    ("theme/project", "api_theme_project", "POST", "主题项目"),
    ("theme/market", "api_theme_market", "POST", "主题市场"),
]
