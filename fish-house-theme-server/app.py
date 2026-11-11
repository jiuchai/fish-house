"""fish-house theme market server.

一个很小的 FastAPI 主题市场：
- 注册 / 登录（Bearer token，SQLite 存会话）
- 上传主题包 .zip（theme.json + videos/ + cover，兼容旧 .fishtheme）
- 公开列表 / 下载 / 封面
- 作者修改、删除自己的主题

启动：
    python -m pip install -r requirements.txt
    python app.py
默认 http://127.0.0.1:8077
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("THEME_DATA_DIR", BASE_DIR / "server-data")).resolve()
THEME_DIR = DATA_DIR / "themes"
COVER_DIR = DATA_DIR / "covers"
DB_PATH = DATA_DIR / "market.db"
MAX_UPLOAD = int(os.environ.get("THEME_MAX_UPLOAD_MB", "200")) * 1024 * 1024
MAX_UNCOMPRESSED = 1024 * 1024 * 1024
SESSION_DAYS = 30

for directory in (DATA_DIR, THEME_DIR, COVER_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="fish-house theme market", version="1.4.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS likes (
                user_id INTEGER NOT NULL,
                theme_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (user_id, theme_id)
            );
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                theme_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (user_id, theme_id)
            );
            CREATE TABLE IF NOT EXISTS themes (
                id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                author TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT '1.0.0',
                filename TEXT NOT NULL,
                size INTEGER NOT NULL,
                cover TEXT NOT NULL DEFAULT '',
                downloads INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "email" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)"
        )


init_db()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return secrets.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash(token), user_id, time.time() + SESSION_DAYS * 86400),
        )
    return token


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "未登录")
    token = authorization.split(" ", 1)[1].strip()
    with db() as conn:
        row = conn.execute(
            """
            SELECT users.id, users.username FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash(token), time.time()),
        ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期")
    return {"id": row["id"], "username": row["username"]}


def current_user_optional(authorization: str | None = Header(default=None)) -> dict | None:
    try:
        return current_user(authorization)
    except HTTPException:
        return None


USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=120)


class LoginBody(BaseModel):
    username: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=120)


class UpdateBody(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=2000)
    version: str | None = Field(default=None, max_length=32)


def theme_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "author": row["author"],
        "description": row["description"],
        "version": row["version"],
        "size": row["size"],
        "downloads": row["downloads"],
        "likes": row["likes"] if "likes" in row.keys() else 0,
        "liked": False,
        "favorited": False,
        "cover_url": f"/api/themes/{row['id']}/cover" if row["cover"] else "",
        "download_url": f"/api/themes/{row['id']}/download",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "mine": False,
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "time": time.time()}


@app.post("/api/register")
def register(body: AuthBody) -> dict:
    username = body.username.strip()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(400, "用户名只能是 3-32 位字母、数字、_ . -")
    email = str(body.email or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(400, "请填写有效邮箱")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, "用户名已存在")
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise HTTPException(409, "邮箱已注册")
        cur = conn.execute(
            "INSERT INTO users (username, password, email, created_at) VALUES (?, ?, ?, ?)",
            (username, hash_password(body.password), email, time.time()),
        )
        user_id = int(cur.lastrowid)
    return {"token": create_session(user_id), "username": username, "email": email}


@app.post("/api/login")
def login(body: LoginBody) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT id, username, email, password FROM users WHERE username = ? OR email = ?",
            (body.username.strip(), body.username.strip().lower()),
        ).fetchone()
    if row is None or not verify_password(body.password, row["password"]):
        raise HTTPException(401, "用户名/邮箱或密码错误")
    return {"token": create_session(int(row["id"])), "username": row["username"], "email": row["email"]}


@app.get("/api/me")
def me(user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM themes WHERE owner_id = ?", (user["id"],)
        ).fetchone()["n"]
    return {"username": user["username"], "themes": count}


@app.get("/api/themes")
def list_themes(
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict | None = Depends(current_user_optional),
) -> dict:
    with db() as conn:
        if q.strip():
            like = f"%{q.strip()}%"
            rows = conn.execute(
                """
                SELECT themes.*, (SELECT COUNT(*) FROM likes WHERE likes.theme_id = themes.id) AS likes
                FROM themes
                WHERE name LIKE ? OR author LIKE ? OR description LIKE ?
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (like, like, like, limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM themes WHERE name LIKE ? OR author LIKE ? OR description LIKE ?",
                (like, like, like),
            ).fetchone()["n"]
        else:
            rows = conn.execute(
                """
                SELECT themes.*, (SELECT COUNT(*) FROM likes WHERE likes.theme_id = themes.id) AS likes
                FROM themes ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM themes").fetchone()["n"]
    uid = user["id"] if user else -1
    liked = set()
    favorited = set()
    if uid > 0:
        with db() as conn:
            liked = {r["theme_id"] for r in conn.execute("SELECT theme_id FROM likes WHERE user_id = ?", (uid,))}
            favorited = {r["theme_id"] for r in conn.execute("SELECT theme_id FROM favorites WHERE user_id = ?", (uid,))}
    items = []
    for row in rows:
        item = theme_row(row)
        item["mine"] = int(row["owner_id"]) == uid
        item["liked"] = item["id"] in liked
        item["favorited"] = item["id"] in favorited
        items.append(item)
    return {"total": total, "items": items}


@app.get("/api/themes/{theme_id}")
def theme_detail(theme_id: str, user: dict | None = Depends(current_user_optional)) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT themes.*, (SELECT COUNT(*) FROM likes WHERE likes.theme_id = themes.id) AS likes FROM themes WHERE id = ?",
            (theme_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "主题不存在")
    item = theme_row(row)
    if user:
        with db() as conn:
            item["liked"] = (
                conn.execute(
                    "SELECT 1 FROM likes WHERE user_id = ? AND theme_id = ?",
                    (user["id"], theme_id),
                ).fetchone()
                is not None
            )
            item["favorited"] = (
                conn.execute(
                    "SELECT 1 FROM favorites WHERE user_id = ? AND theme_id = ?",
                    (user["id"], theme_id),
                ).fetchone()
                is not None
            )
        item["mine"] = int(row["owner_id"]) == user["id"]
    return item


@app.get("/api/themes/{theme_id}/download")
def download_theme(theme_id: str) -> FileResponse:
    with db() as conn:
        row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "主题不存在")
    path = THEME_DIR / row["filename"]
    if not path.is_file():
        raise HTTPException(404, "文件已丢失")
    with db() as conn:
        conn.execute("UPDATE themes SET downloads = downloads + 1 WHERE id = ?", (theme_id,))
    return FileResponse(path, media_type="application/zip", filename=f"{row['name']}.zip")


@app.get("/api/themes/{theme_id}/cover")
def theme_cover(theme_id: str) -> FileResponse:
    with db() as conn:
        row = conn.execute("SELECT cover FROM themes WHERE id = ?", (theme_id,)).fetchone()
    if row is None or not row["cover"]:
        raise HTTPException(404, "没有封面")
    path = COVER_DIR / row["cover"]
    if not path.is_file():
        raise HTTPException(404, "封面已丢失")
    return FileResponse(path)


def read_theme_json(zpath: Path) -> dict:
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            if "theme.json" not in names:
                raise HTTPException(400, "缺少 theme.json")
            if len(names) > 500:
                raise HTTPException(400, "文件数量过多")
            total = 0
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise HTTPException(400, "压缩包路径不安全")
                if name != "theme.json" and not (
                    name.startswith("videos/") or name.startswith("cover")
                ):
                    raise HTTPException(400, f"不允许的文件：{name}")
                total += info.file_size
                if total > MAX_UNCOMPRESSED:
                    raise HTTPException(400, "解压后体积过大")
            raw = zf.read("theme.json")
    except zipfile.BadZipFile:
        raise HTTPException(400, "不是有效的 zip/主题包")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"读取主题失败：{exc}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "theme.json 不是 UTF-8 JSON")
    if not isinstance(data, dict) or int(data.get("schema") or 0) != 1:
        raise HTTPException(400, "不支持的 theme schema")
    if not isinstance(data.get("config"), dict):
        raise HTTPException(400, "theme.config 缺失")
    return data


@app.post("/api/themes")
async def create_theme(
    file: UploadFile = File(...),
    name: str = Form(default=""),
    description: str = Form(default=""),
    version: str = Form(default=""),
    user: dict = Depends(current_user),
) -> dict:
    theme_id = uuid.uuid4().hex[:12]
    target = THEME_DIR / f"{theme_id}.zip"
    size = 0
    with target.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, "主题包过大")
            out.write(chunk)
    try:
        theme = read_theme_json(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    meta = theme.get("meta") if isinstance(theme.get("meta"), dict) else {}
    title = str(meta.get("name") or name or "未命名主题")[:60]
    author = str(meta.get("author") or user["username"])[:40]
    desc = str(meta.get("description") or description or "")[:2000]
    ver = str(meta.get("version") or version or "1.0.0")[:32]
    cover_name = ""
    cover_field = str(meta.get("cover") or "")
    if cover_field:
        try:
            with zipfile.ZipFile(target) as zf:
                if cover_field in zf.namelist() and cover_field.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".webp")
                ):
                    ext = Path(cover_field).suffix.lower()
                    cover_name = f"{theme_id}{ext}"
                    (COVER_DIR / cover_name).write_bytes(zf.read(cover_field))
        except Exception:
            cover_name = ""
    now = time.time()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO themes
            (id, owner_id, name, author, description, version, filename, size, cover, downloads, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (theme_id, user["id"], title, author, desc, ver, target.name, size, cover_name, now, now),
        )
        row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
    item = theme_row(row)
    item["mine"] = True
    return item


@app.post("/api/themes/{theme_id}/like")
def toggle_like(theme_id: str, user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        if conn.execute("SELECT 1 FROM themes WHERE id = ?", (theme_id,)).fetchone() is None:
            raise HTTPException(404, "主题不存在")
        exists = conn.execute(
            "SELECT 1 FROM likes WHERE user_id = ? AND theme_id = ?", (user["id"], theme_id)
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM likes WHERE user_id = ? AND theme_id = ?", (user["id"], theme_id))
            liked = False
        else:
            conn.execute(
                "INSERT INTO likes (user_id, theme_id, created_at) VALUES (?, ?, ?)",
                (user["id"], theme_id, time.time()),
            )
            liked = True
        count = conn.execute("SELECT COUNT(*) AS n FROM likes WHERE theme_id = ?", (theme_id,)).fetchone()["n"]
    return {"liked": liked, "likes": count}


@app.post("/api/themes/{theme_id}/favorite")
def toggle_favorite(theme_id: str, user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        if conn.execute("SELECT 1 FROM themes WHERE id = ?", (theme_id,)).fetchone() is None:
            raise HTTPException(404, "主题不存在")
        exists = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND theme_id = ?", (user["id"], theme_id)
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM favorites WHERE user_id = ? AND theme_id = ?", (user["id"], theme_id))
            favorited = False
        else:
            conn.execute(
                "INSERT INTO favorites (user_id, theme_id, created_at) VALUES (?, ?, ?)",
                (user["id"], theme_id, time.time()),
            )
            favorited = True
    return {"favorited": favorited}


def _my_themes(kind: str, user: dict) -> dict:
    with db() as conn:
        if kind == "published":
            rows = conn.execute(
                "SELECT themes.*, (SELECT COUNT(*) FROM likes WHERE likes.theme_id = themes.id) AS likes "
                "FROM themes WHERE owner_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
        elif kind == "favorites":
            rows = conn.execute(
                "SELECT themes.*, (SELECT COUNT(*) FROM likes WHERE likes.theme_id = themes.id) AS likes "
                "FROM favorites JOIN themes ON themes.id = favorites.theme_id "
                "WHERE favorites.user_id = ? ORDER BY favorites.created_at DESC",
                (user["id"],),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT themes.*, (SELECT COUNT(*) FROM likes WHERE likes.theme_id = themes.id) AS likes "
                "FROM likes JOIN themes ON themes.id = likes.theme_id "
                "WHERE likes.user_id = ? ORDER BY likes.created_at DESC",
                (user["id"],),
            ).fetchall()
    items = []
    for row in rows:
        item = theme_row(row)
        item["mine"] = int(row["owner_id"]) == user["id"]
        item["liked"] = kind == "likes"
        item["favorited"] = kind == "favorites"
        items.append(item)
    return {"items": items, "total": len(items)}


@app.get("/api/me/{kind}")
def my_themes(kind: str, user: dict = Depends(current_user)) -> dict:
    if kind not in {"published", "favorites", "likes"}:
        raise HTTPException(404, "未知列表")
    return _my_themes(kind, user)


@app.patch("/api/themes/{theme_id}")
def update_theme(theme_id: str, body: UpdateBody, user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "主题不存在")
        if int(row["owner_id"]) != user["id"]:
            raise HTTPException(403, "只能修改自己的主题")
        fields = []
        values = []
        for key in ("name", "description", "version"):
            value = getattr(body, key)
            if value is not None:
                fields.append(f"{key} = ?")
                values.append(value.strip())
        if fields:
            values.extend([time.time(), theme_id])
            conn.execute(f"UPDATE themes SET {', '.join(fields)}, updated_at = ? WHERE id = ?", values)
        row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
    item = theme_row(row)
    item["mine"] = True
    return item


@app.post("/api/themes/{theme_id}/file")
async def update_theme_file(
    theme_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "主题不存在")
        if int(row["owner_id"]) != user["id"]:
            raise HTTPException(403, "只能更新自己的主题")
    temp = THEME_DIR / f".{theme_id}.upload"
    size = 0
    try:
        with temp.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "主题包过大")
                out.write(chunk)
        read_theme_json(temp)
        os.replace(temp, THEME_DIR / row["filename"])
    finally:
        temp.unlink(missing_ok=True)
    # 新包带了封面就一起更新，避免旧封面和内容对不上
    cover_name = str(row["cover"] or "")
    try:
        theme = read_theme_json(THEME_DIR / row["filename"])
        cover_field = str((theme.get("meta") or {}).get("cover") or "")
        if cover_field and cover_field.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            with zipfile.ZipFile(THEME_DIR / row["filename"]) as zf:
                if cover_field in zf.namelist():
                    ext = Path(cover_field).suffix.lower()
                    new_cover = f"{theme_id}{ext}"
                    (COVER_DIR / new_cover).write_bytes(zf.read(cover_field))
                    if cover_name and cover_name != new_cover:
                        (COVER_DIR / cover_name).unlink(missing_ok=True)
                    cover_name = new_cover
    except Exception:
        pass
    with db() as conn:
        conn.execute(
            "UPDATE themes SET size = ?, cover = ?, updated_at = ? WHERE id = ?",
            (size, cover_name, time.time(), theme_id),
        )
        row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
    item = theme_row(row)
    item["mine"] = True
    return item


@app.delete("/api/themes/{theme_id}")
def delete_theme(theme_id: str, user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "主题不存在")
        if int(row["owner_id"]) != user["id"]:
            raise HTTPException(403, "只能删除自己的主题")
        conn.execute("DELETE FROM themes WHERE id = ?", (theme_id,))
    (THEME_DIR / row["filename"]).unlink(missing_ok=True)
    if row["cover"]:
        (COVER_DIR / row["cover"]).unlink(missing_ok=True)
    return {"deleted": theme_id}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": str(exc.detail), "data": {}},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("THEME_HOST", "0.0.0.0"),
        port=int(os.environ.get("THEME_PORT", "8077")),
    )