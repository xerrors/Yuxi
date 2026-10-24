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

# 复制项目配置文件。显式 --chown：构建上下文若为 0600 等严格权限，
# 默认 root 属主会让 USER 1000 读失败（#1109）。
COPY --chown=1000:1000 backend/pyproject.toml /app/pyproject.toml
COPY --chown=1000:1000 backend/.python-version /app/.python-version
COPY --chown=1000:1000 backend/uv.lock /app/uv.lock

# 先复制 package 目录，因为 pyproject.toml 中 yuxi = { path = "package", editable = true }
COPY --chown=1000:1000 backend/package /app/package

# 如果网络还是不好，可以在后面添加 --index-url https://pypi.tuna.tsinghua.edu.cn/simple
RUN uv sync --no-cache --group test --no-dev --frozen

# 复制 server 代码
COPY --chown=1000:1000 backend/server /app/server
COPY --chown=1000:1000 docker/api-entrypoint.sh /usr/local/bin/yuxi-entrypoint

# RapidOCR 解析器使用 rapidocr 包内自带的 PP-OCRv6 模型；若不预置，首次解析会联网到
# modelscope.cn 下载且离线部署不可行，因此构建期把模型预置进 RAPIDOCR_MODEL_DIR 指向的目录。
RUN groupadd --gid 1000 yuxi \
    && useradd --uid 1000 --gid 1000 --create-home yuxi \
    && mkdir -p /app/runtime /home/yuxi/.cache/rapidocr/models \
    && cp "$(python -c 'import os, sysconfig; print(os.path.join(sysconfig.get_paths()["purelib"], "rapidocr", "models"))')"/*.onnx /home/yuxi/.cache/rapidocr/models/ \
    && chown -R 1000:1000 /app/runtime /home/yuxi /app/package \
    && chmod 0755 /usr/local/bin/yuxi-entrypoint

ENV HOME=/home/yuxi \
    RAPIDOCR_MODEL_DIR=/home/yuxi/.cache/rapidocr/models

USER 1000:1000

ENTRYPOINT ["/usr/local/bin/yuxi-entrypoint"]
