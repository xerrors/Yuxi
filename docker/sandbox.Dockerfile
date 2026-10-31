# Yuxi 轻量沙盒镜像：单租户、非 root、按 Run 用完即弃。
FROM node:24-bullseye-slim AS node-runtime

FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        file \
        git \
        iproute2 \
        jq \
        libcap2-bin \
        procps \
        ripgrep \
        unzip \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# 固定普通用户与现有 Workspace 文件身份一致；/usr/bin/python3 供 runner 的 python 模式使用。
RUN groupadd --gid 1000 user \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash user \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3 \
    && ln -sf /usr/local/bin/pip3 /usr/bin/pip3 \
    && mkdir -p /home/user/workspace /home/user/skills \
    && chown -R 1000:1000 /home/user

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

RUN pip install --no-cache-dir \
        beautifulsoup4 \
        httpx \
        openpyxl \
        pillow \
        pymysql \
        requests

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /usr/local/bin/

USER 1000:1000
WORKDIR /home/user
CMD ["sleep", "infinity"]
