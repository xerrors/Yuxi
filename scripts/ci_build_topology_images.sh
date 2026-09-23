#!/usr/bin/env bash
# 预构建 Runtime System Tests 所需镜像并启用 GitHub Actions 层缓存。
# api/worker/storage-migrator 共用 docker/api.Dockerfile 产出的同一镜像，只需构建一次。
# 镜像名从 compose 配置解析，跟随 docker-compose.yml 与 .env.template，不硬编码版本。
# 冷运行（无缓存）与 docker compose build 等价；热运行命中 apt、uv sync、pip 层。
set -euo pipefail

api_image=$(docker compose config --format json | jq -r '.services.api.image')
provisioner_image=$(docker compose config --format json | jq -r '.services["sandbox-provisioner"].image')
test -n "$api_image" && test -n "$provisioner_image"

docker buildx build \
  --file docker/api.Dockerfile \
  --tag "$api_image" \
  --cache-from type=gha,scope=yuxi-ci-api \
  --cache-to type=gha,mode=max \
  --load .
docker buildx build \
  --file docker/sandbox_provisioner/Dockerfile \
  --tag "$provisioner_image" \
  --cache-from type=gha,scope=yuxi-ci-sandbox-provisioner \
  --cache-to type=gha,mode=max \
  --load ./docker/sandbox_provisioner
