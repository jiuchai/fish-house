"""Theme package helpers for astrbot_plugin_fish_house.

`.fishtheme` = zip(theme.json, videos/*, cover.*).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

SCHEMA = 1
MAX_FILES = 500
MAX_UNCOMPRESSED = 1024 * 1024 * 1024


def safe_name(text: str, fallback: str = "theme") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_")
    return value[:60] or fallback


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]


def _local_file(url: str, video_dir: Path) -> Path | None:
    value = str(url or "").strip()
    if not value or value.startswith(("http://", "https://", "data:", "blob:")):
        return None
    if value.startswith("/"):
        parts = value.strip("/").split("/")
        if "videos" in parts:
            candidate = video_dir / parts[-1]
            return candidate if candidate.is_file() else None
        return None
    candidate = video_dir / Path(value).name
    return candidate if candidate.is_file() else None


def _media_url(item: dict[str, Any], library: dict[str, dict[str, Any]]) -> str:
    mid = str(item.get("media_id") or "")
    if mid and mid in library:
        return str(library[mid].get("video") or "")
    return str(item.get("video") or "")


def collect_video_map(
    snapshot: dict[str, Any], video_dir: Path
) -> tuple[dict[str, str], list[tuple[Path, str]]]:
    library = {str(x.get("id")): x for x in snapshot.get("library") or [] if x.get("id")}
    urls: list[str] = [str(v) for v in (snapshot.get("videos") or {}).values()]
    urls += [str(x.get("video") or "") for x in snapshot.get("library") or []]
    urls += [
        _media_url(n, library)
        for n in (snapshot.get("flow") or {}).get("nodes") or []
    ]
    mapping: dict[str, str] = {}
    files: list[tuple[Path, str]] = []
    for url in urls:
        if not url or url in mapping:
            continue
        local = _local_file(url, video_dir)
        if local is None:
            continue
        arc = f"videos/{safe_name(local.stem, 'video')}-{_hash(url)}{local.suffix.lower() or '.mp4'}"
        mapping[url] = arc
        files.append((local, arc))
    return mapping, files


def build_theme(
    snapshot: dict[str, Any],
    meta: dict[str, Any],
    themes_dir: Path,
    video_dir: Path,
    include_videos: bool = True,
    cover: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    themes_dir.mkdir(parents=True, exist_ok=True)
    mapping, files = collect_video_map(snapshot, video_dir) if include_videos else ({}, [])
    library_by_id = {
        str(x.get("id")): x for x in snapshot.get("library") or [] if x.get("id")
    }

    def remap(url: str) -> str:
        return mapping.get(str(url), str(url))

    library = []
    for item in snapshot.get("library") or []:
        copy = dict(item)
        copy["video"] = remap(str(item.get("video") or ""))
        library.append(copy)

    flow = json.loads(json.dumps(snapshot.get("flow") or {"nodes": [], "edges": []}))
    for node in flow.get("nodes") or []:
        node["video"] = remap(_media_url(node, library_by_id))
        node.pop("outfit", None)

    theme_id = safe_name(str(meta.get("id") or uuid.uuid4().hex[:10]), "theme")
    theme = {
        "schema": SCHEMA,
        "meta": {
            "id": theme_id,
            "name": str(meta.get("name") or "未命名主题")[:60],
            "author": str(meta.get("author") or "")[:40],
            "description": str(meta.get("description") or "")[:2000],
            "version": str(meta.get("version") or "1.0.0")[:32],
            "created_at": float(time.time()),
            "cover": "",
        },
        "config": {
            "actions": [dict(x) for x in snapshot.get("actions") or []],
            "videos": {str(k): remap(str(v)) for k, v in (snapshot.get("videos") or {}).items()},
            "audio": {str(k): bool(v) for k, v in (snapshot.get("audio") or {}).items()},
            "durations": {
                str(k): float(v or 0) for k, v in (snapshot.get("durations") or {}).items()
            },
            "library": library,
            "flow": flow,
            "settings": {
                "crossfade_ms": int((snapshot.get("settings") or {}).get("crossfade_ms", 400))
            },
        },
    }
    if cover and cover.is_file() and cover.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        theme["meta"]["cover"] = "cover" + cover.suffix.lower()

    zip_path = themes_dir / f"{theme_id}.fishtheme"
    tmp = zip_path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("theme.json", json.dumps(theme, ensure_ascii=False, indent=2))
        for local, arc in files:
            zf.write(local, arc)
        if theme["meta"]["cover"]:
            zf.write(cover, theme["meta"]["cover"])
    tmp.replace(zip_path)
    return zip_path, theme


def _safe_names(zf: zipfile.ZipFile) -> None:
    names = zf.namelist()
    if len(names) > MAX_FILES:
        raise ValueError("压缩包文件过多")
    total = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError("压缩包路径不安全")
        if name != "theme.json" and not (
            name.startswith("videos/") or name.startswith("cover")
        ):
            raise ValueError(f"不允许的文件：{name}")
        total += info.file_size
        if total > MAX_UNCOMPRESSED:
            raise ValueError("解压后体积过大")


def _validate_theme(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or int(data.get("schema") or 0) != SCHEMA:
        raise ValueError("不支持的 theme schema")
    if not isinstance(data.get("config"), dict):
        raise ValueError("theme.config 缺失")
    return data


def read_theme(zip_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _safe_names(zf)
            if "theme.json" not in zf.namelist():
                raise ValueError("缺少 theme.json")
            data = json.loads(zf.read("theme.json").decode("utf-8"))
    except zipfile.BadZipFile:
        raise ValueError("不是有效的 fishtheme/zip")
    return _validate_theme(data)


def read_theme_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _validate_theme(data)


def install_theme(zip_path: Path, themes_dir: Path) -> dict[str, Any]:
    theme = read_theme(zip_path)
    meta = theme.get("meta") if isinstance(theme.get("meta"), dict) else {}
    tid = safe_name(str(meta.get("id") or uuid.uuid4().hex[:10]), "theme")
    target = themes_dir / tid
    if target.exists():
        tid = f"{tid}-{uuid.uuid4().hex[:6]}"
        target = themes_dir / tid
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            if name == "theme.json" or name.startswith("videos/") or name.startswith("cover"):
                dest = target / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(name))
    theme = read_theme_json(target / "theme.json")
    theme.setdefault("meta", {})["id"] = tid
    (target / "theme.json").write_text(
        json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    saved = themes_dir / f"{tid}.fishtheme"
    if zip_path.resolve() != saved.resolve():
        shutil.copy2(zip_path, saved)
    return {"id": tid, "dir": str(target), "theme": theme, "zip": str(saved)}


def list_installed(themes_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not themes_dir.is_dir():
        return items
    for path in sorted(themes_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir():
            continue
        theme_file = path / "theme.json"
        if not theme_file.is_file():
            continue
        try:
            theme = json.loads(theme_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = theme.get("meta") or {}
        zip_file = themes_dir / f"{path.name}.fishtheme"
        items.append(
            {
                "id": path.name,
                "name": str(meta.get("name") or path.name),
                "author": str(meta.get("author") or ""),
                "description": str(meta.get("description") or ""),
                "version": str(meta.get("version") or "1.0.0"),
                "created_at": float(meta.get("created_at") or 0),
                "cover": str(meta.get("cover") or ""),
                "size": zip_file.stat().st_size if zip_file.is_file() else 0,
            }
        )
    return items


def materialize_theme(
    theme: dict[str, Any], theme_dir: Path, video_dir: Path, theme_id: str
) -> dict[str, Any]:
    config = theme.get("config") or {}
    video_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}

    def rewrite(url: str) -> str:
        value = str(url or "")
        if not value.startswith("videos/"):
            return value
        if value in copied:
            return copied[value]
        source = theme_dir / value
        if not source.is_file():
            return ""
        suffix = source.suffix.lower() or ".mp4"
        dest = video_dir / (
            f"theme_{safe_name(theme_id, 't')}_{safe_name(source.stem, 'video')}{suffix}"
        )
        index = 1
        while dest.exists():
            dest = video_dir / f"{dest.stem}-{index}{suffix}"
            index += 1
        shutil.copy2(source, dest)
        copied[value] = f"videos/{dest.name}"
        return copied[value]

    library = []
    id_map: dict[str, str] = {}
    for item in config.get("library") or []:
        new_id = f"m_{uuid.uuid4().hex[:8]}"
        old_id = str(item.get("id") or "")
        if old_id:
            id_map[old_id] = new_id
        library.append(
            {
                "id": new_id,
                "name": str(item.get("name") or "素材")[:40],
                "video": rewrite(str(item.get("video") or "")),
                "duration": max(0.0, float(item.get("duration") or 0)),
            }
        )

    flow = json.loads(json.dumps(config.get("flow") or {"nodes": [], "edges": []}))
    for node in flow.get("nodes") or []:
        node["video"] = rewrite(str(node.get("video") or ""))
        node["media_id"] = id_map.get(str(node.get("media_id") or ""), "")
        node.pop("outfit", None)

    return {
        "actions": [dict(x) for x in config.get("actions") or []],
        "videos": {str(k): rewrite(str(v)) for k, v in (config.get("videos") or {}).items()},
        "audio": {str(k): bool(v) for k, v in (config.get("audio") or {}).items()},
        "durations": {
            str(k): float(v or 0) for k, v in (config.get("durations") or {}).items()
        },
        "library": library,
        "flow": flow,
        "settings": {
            "crossfade_ms": int((config.get("settings") or {}).get("crossfade_ms", 400))
        },
        "meta": theme.get("meta") or {},
    }
