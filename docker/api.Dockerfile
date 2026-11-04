# 使用轻量级Python基础镜像
FROM python:3.13-slim
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
    # (B) 替换清华源 (针对 Debian Bookworm 的新版格式)
    && sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|security.debian.org/debian-security|mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    # (C) 安装必要的系统库
    && apt-get update \
    && apt-get install -y --no-install-recommends --fix-missing \
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
