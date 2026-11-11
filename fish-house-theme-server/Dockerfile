FROM python:3.12-slim

WORKDIR /app

# 默认用国内镜像；海外服务器可传 --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
ENV THEME_HOST=0.0.0.0 \
    THEME_PORT=8077 \
    THEME_DATA_DIR=/data
EXPOSE 8077
VOLUME ["/data"]
CMD ["python", "app.py"]
