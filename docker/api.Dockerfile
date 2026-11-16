# 使用轻量级Python基础镜像
FROM python:3.13-slim
ARG TARGETARCH
ARG CLOUDFLARED_VERSION=2026.10.0
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/
COPY --from=node:24-slim /usr/local/bin /usr/local/bin
COPY --from=node:24-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node:24-slim /usr/local/include /usr/local/include
COPY --from=node:24-slim /usr/local/share /usr/local/share

# 设置工作目录
WORKDIR /app

# 环境变量设置
ENV TZ=Asia/Shanghai \
    UV_PROJECT_ENVIRONMENT="/usr/local" \
    UV_COMPILE_BYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# 设置 npm 镜像源，为 MCP 和 Skills 安装依赖
RUN npm config set registry https://registry.npmmirror.com --global \
    && npm cache clean --force

# 设置代理和时区，更换镜像源，安装系统依赖 - 合并为一个RUN减少层数
RUN set -ex \
    # (A) 设置时区
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    # (B) 使用基础镜像默认的软件源，避免把构建绑定到单一镜像站点
    # (C) 安装必要的系统库
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends --fix-missing \
        curl \
        ffmpeg \
        fonts-liberation \
        fonts-noto-cjk \
        git \
        libpq5 \
        libsm6 \
        libxext6 \
        libreoffice-calc-nogui \
        libreoffice-impress-nogui \
        libreoffice-writer-nogui \
    # (D) 清理垃圾，减小体积
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 按架构校验命名隧道客户端，API 仅在用户请求开发联调时启动。
RUN set -eux; \
    arch="${TARGETARCH:-amd64}"; \
    case "$arch" in \
      amd64) asset=cloudflared-linux-amd64; sha256=d33ff2d14475178d2012c2c56beba87389ac5ded27649519f198a7d3134a99db ;; \
      arm64) asset=cloudflared-linux-arm64; sha256=e6422b9d4f72d3194bc5a38676f13667c06666523217b842a877d72a80b5ac08 ;; \
      *) echo "unsupported TARGETARCH: $arch" >&2; exit 1 ;; \
    esac; \
    curl --fail --silent --show-error --location --retry 3 \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/${asset}" \
      --output /usr/local/bin/cloudflared; \
    echo "${sha256}  /usr/local/bin/cloudflared" | sha256sum --check --status; \
    chmod 0755 /usr/local/bin/cloudflared

# 复制项目配置文件
COPY --chown=0:0 --chmod=0644 backend/pyproject.toml /app/pyproject.toml
COPY --chown=0:0 --chmod=0644 backend/.python-version /app/.python-version
COPY --chown=0:0 --chmod=0644 backend/uv.lock /app/uv.lock

# 安装唯一的后端 Python 项目。
COPY --chown=0:0 --chmod=0644 backend/README.md /app/README.md
COPY --chown=0:0 --chmod=0644 backend/MANIFEST.in /app/MANIFEST.in
COPY --chown=0:0 backend/yuxi /app/yuxi
# 构建输入可来自 600/700 checkout，镜像内源码只读权限不依赖宿主模式。
RUN find /app/yuxi -type d -exec chmod 0755 {} + \
    && find /app/yuxi -type f -exec chmod 0644 {} +

# 依赖版本与发行文件下载地址由锁文件固定。
RUN uv sync --no-cache --group test --no-dev --frozen

COPY --chown=0:0 --chmod=0755 docker/api-entrypoint.sh /usr/local/bin/yuxi-entrypoint

# RapidOCR 使用 rapidocr wheel 自带的 PP-OCRv6 模型；不预置时首次解析会联网到
# modelscope.cn 下载且离线部署不可行，因此构建期预置到 RAPIDOCR_MODEL_DIR 指向目录。
RUN groupadd --gid 1000 yuxi \
    && useradd --uid 1000 --gid 1000 --create-home yuxi \
    && mkdir -p /app/runtime /home/yuxi/.cache/rapidocr/models \
    && cp "$(python -c 'import os, sysconfig; print(os.path.join(sysconfig.get_paths()["purelib"], "rapidocr", "models"))')"/*.onnx /home/yuxi/.cache/rapidocr/models/ \
    && chown -R 1000:1000 /app/runtime /home/yuxi

ENV HOME=/home/yuxi \
    RAPIDOCR_MODEL_DIR=/home/yuxi/.cache/rapidocr/models

USER 1000:1000

ENTRYPOINT ["/usr/local/bin/yuxi-entrypoint"]
