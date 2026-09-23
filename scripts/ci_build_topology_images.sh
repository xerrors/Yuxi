#!/usr/bin/env bash
# 预构建 Runtime System Tests 所需镜像并启用层缓存。
# api/worker/storage-migrator 共用 docker/api.Dockerfile 产出的同一镜像，只需构建一次。
# 镜像名从 compose 配置解析，跟随 docker-compose.yml 与 .env.template，不硬编码版本。
# $1 为可选的 buildkit 本地缓存目录：提供时用 type=local 读写层缓存（由 actions/cache 持久化），
# 不提供时退化为无缓存构建（与 docker compose build 等价）。
set -euo pipefail

api_image=$(docker compose config --format json | jq -r '.services.api.image')
provisioner_image=$(docker compose config --format json | jq -r '.services["sandbox-provisioner"].image')
test -n "$api_image" && test -n "$provisioner_image"

cache_dir=${1:-}
cache_flags=()
if [ -n "$cache_dir" ]; then
  mkdir -p "$cache_dir"
  cache_flags=(--cache-from "type=local,src=$cache_dir" --cache-to "type=local,dest=$cache_dir,mode=max")
fi

docker buildx build \
  --file docker/api.Dockerfile \
  --tag "$api_image" \
  "${cache_flags[@]}" \
  --load .
docker buildx build \
  --file docker/sandbox_provisioner/Dockerfile \
  --tag "$provisioner_image" \
  "${cache_flags[@]}" \
  --load ./docker/sandbox_provisioner
