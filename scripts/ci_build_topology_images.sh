#!/usr/bin/env bash
# 预构建 Runtime System Tests 所需镜像并启用层缓存。
# api/worker/storage-migrator 共用 docker/api.Dockerfile 产出的同一镜像，只需构建一次。
# 镜像名从 compose 配置解析，跟随 docker-compose.yml 与 .env.template，不硬编码版本。
# $1/$2 为可选的 buildkit 本地缓存目录（分别对应 api 与 sandbox-provisioner 镜像）：
# type=local 缓存的 manifest 槽位按镜像隔离，两个镜像不得共用目录，否则后导出者覆盖前者。
# 不提供目录时退化为无缓存构建（与 docker compose build 等价）。
set -euo pipefail

api_image=$(docker compose config --format json | jq -r '.services.api.image')
provisioner_image=$(docker compose config --format json | jq -r '.services["sandbox-provisioner"].image')
test -n "$api_image" && test -n "$provisioner_image"

build_with_cache() {
  local dockerfile=$1 tag=$2 context=$3 cache_dir=$4
  local cache_flags=()
  if [ -n "$cache_dir" ]; then
    mkdir -p "$cache_dir"
    cache_flags=(--cache-from "type=local,src=$cache_dir" --cache-to "type=local,dest=$cache_dir,mode=max")
  fi
  docker buildx build \
    --file "$dockerfile" \
    --tag "$tag" \
    "${cache_flags[@]}" \
    --load "$context"
}

build_with_cache docker/api.Dockerfile "$api_image" . "${1:-}"
build_with_cache docker/sandbox_provisioner/Dockerfile "$provisioner_image" ./docker/sandbox_provisioner "${2:-}"
