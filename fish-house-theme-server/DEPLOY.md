# 部署到服务器

## 方式一：Docker（推荐）

把整个 `fish-house-theme-server` 目录传到服务器，然后：

```bash
cd fish-house-theme-server
cp .env.example .env
# 直连保持 THEME_BIND=0.0.0.0；挂 Nginx 后改成 127.0.0.1
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8077/api/health
```

数据会落在同目录的 `server-data/`。默认监听 `0.0.0.0:8077`，可以直接访问 `http://服务器IP:8077`。如果前面挂 Nginx/HTTPS，把 `.env` 里的 `THEME_BIND` 改成 `127.0.0.1`。

## 方式二：直接跑 Python

```bash
cd fish-house-theme-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export THEME_HOST=127.0.0.1
export THEME_PORT=8077
export THEME_DATA_DIR=/opt/fish-house/server-data
python app.py
```

用 systemd 托管示例：

```ini
[Unit]
Description=fish-house theme market
After=network.target

[Service]
WorkingDirectory=/opt/fish-house/fish-house-theme-server
Environment=THEME_HOST=127.0.0.1
Environment=THEME_PORT=8077
Environment=THEME_DATA_DIR=/opt/fish-house/server-data
ExecStart=/opt/fish-house/fish-house-theme-server/.venv/bin/python app.py
Restart=always
RestartSec=3
User=www-data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fish-house-market
```

## Nginx + HTTPS

```nginx
server {
    listen 443 ssl http2;
    server_name market.example.com;

    ssl_certificate     /etc/letsencrypt/live/market.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/market.example.com/privkey.pem;

    client_max_body_size 200m;

    location / {
        proxy_pass http://127.0.0.1:8077;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

证书可用 certbot：

```bash
sudo certbot --nginx -d market.example.com
```

## 上线后

1. 面板 → 设置 → 主题市场，把地址改成 `https://market.example.com`；
2. 注册一个账号，确认能登录、上传、下载；
3. 不要直接对公网暴露 `8077`，只让 Nginx 反代；
4. 定期备份 `server-data/`（SQLite + 主题包 + 封面）。


## 常见错误

### `project name must not be empty`

目录名含中文等非 ASCII 字符时，Compose 推导项目名为空。两种解决：

```bash
# 方式一：显式指定项目名（所有 compose 命令都要带 -p）
docker compose -p fishhouse up -d --build
docker compose -p fishhouse ps
docker compose -p fishhouse logs -f

# 方式二：把目录改成英文
mv 肥鱼主题服务器 fish-house-market
cd fish-house-market
docker compose up -d --build
```

新版部署包已在 `docker-compose.yml` 顶部写入 `name: fish-house-market`，重新解压后可直接 `docker compose up -d --build`。

### 容器启动但访问不了

```bash
docker compose ps
docker compose logs -f
curl http://127.0.0.1:8077/api/health
```

确认云服务器安全组/防火墙已放行 `8077`，或改用 Nginx 反代。


### 卡在 `pip install` / 下载只有十几 KB/s

默认已改用清华 PyPI 镜像构建。如果仍然慢，先中断构建，再显式指定镜像：

```bash
docker compose -p fishhouse down
docker compose -p fishhouse build --no-cache \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
docker compose -p fishhouse up -d
```

阿里云镜像备用：

```bash
docker compose -p fishhouse build --no-cache \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

海外服务器用官方源：

```bash
docker compose -p fishhouse build --no-cache \
  --build-arg PIP_INDEX_URL=https://pypi.org/simple
```
