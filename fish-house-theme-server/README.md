# fish-house 主题市场（FastAPI）

给 `astrbot_plugin_fish_house` 用的极简主题市场服务端：注册登录、上传/下载/修改/删除自己的主题。

## 启动

```bash
cd fish-house-theme-server
python -m pip install -r requirements.txt
python app.py
```

Docker 部署：

```bash
docker compose up -d --build
```

默认监听 `0.0.0.0:8077`，数据在 `server-data/`（SQLite + 主题包 + 封面）。

环境变量：

- `THEME_PORT` 端口，默认 `8077`
- `THEME_HOST` 监听地址，默认 `0.0.0.0`
- `THEME_DATA_DIR` 数据目录
- `THEME_MAX_UPLOAD_MB` 单包上限，默认 `200`

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/register` | `{"username","password","email"}` → token，邮箱唯一 |
| POST | `/api/login` | `{"username","password"}`，username 可填用户名或邮箱 → token |
| GET | `/api/me` | 当前用户 |
| GET | `/api/themes?q=` | 公开主题列表 |
| GET | `/api/themes/{id}` | 主题详情 |
| GET | `/api/themes/{id}/download` | 下载主题包 `.zip` |
| GET | `/api/themes/{id}/cover` | 封面 |
| POST | `/api/themes` | 登录后 multipart 上传：`file` + 可选 `name` / `description` / `version` |
| POST | `/api/themes/{id}/file` | 作者替换自己的主题包（multipart `file`） |
| POST | `/api/themes/{id}/like` | 点赞/取消 |
| POST | `/api/themes/{id}/favorite` | 收藏/取消 |
| GET | `/api/me/{published\|favorites\|likes}` | 我的发布/收藏/点赞 |
| PATCH | `/api/themes/{id}` | 作者改名称/描述/版本 |
| DELETE | `/api/themes/{id}` | 作者删除 |

上传的主题包是 zip（`.zip`，旧的 `.fishtheme` 一样能传）：根目录 `theme.json`，视频放 `videos/`，封面可以叫 `cover.jpg`/`cover.png`/`cover.webp`。

## 安全说明

- 密码用 PBKDF2-SHA256（20 万次）存储；
- 登录 token 只存 SHA-256 哈希，30 天过期；
- 上传会校验 zip 路径、文件白名单和体积；
- 注册邮箱只做唯一标识，目前没有发信验证、找回密码、限流和审核；公网部署务必加 HTTPS、反向代理和速率限制。
- 部署到服务器见 [DEPLOY.md](DEPLOY.md)。
