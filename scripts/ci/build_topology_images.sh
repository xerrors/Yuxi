#!/usr/bin/env bash
# 预构建 Runtime System Tests 所需镜像并启用层缓存。
# api/worker/app-init 共用 docker/api.Dockerfile 产出的同一镜像，只需构建一次。
# 镜像名从 compose 配置解析，跟随 docker-compose.yml 与 .env.template，不硬编码版本。
# $1/$2/$3 为可选的 buildkit 本地缓存目录（分别对应 api、minio 与 sandbox 镜像）：
# type=local 缓存的 manifest 槽位按镜像隔离，各镜像不得共用目录，否则后导出者覆盖前者。
# 不提供目录时退化为无缓存构建（与 docker compose build 等价）。
# 追加 --sandbox-only 时只构建 sandbox 镜像（供不需要拓扑的宿主沙盒测试复用）。
set -euo pipefail

sandbox_only=false
for arg in "$@"; do
    if [[ "$arg" == "--sandbox-only" ]]; then
        sandbox_only=true
    fi
done

api_image=$(docker compose config --format json | jq -r '.services.api.image')
minio_image=$(docker compose config --format json | jq -r '.services.minio.image')
storage_image=$(docker compose config --format json | jq -r '.services["storage-init"].image')
sandbox_image=$(docker compose config --format json | jq -r '.services["sandbox-image"].image')
test -n "$api_image" && test -n "$minio_image" && test -n "$storage_image" && test "$sandbox_image" != null

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

if ! $sandbox_only; then
    build_with_cache docker/api.Dockerfile "$api_image" . "${1:-}"
    build_with_cache docker/minio/Dockerfile "$minio_image" ./docker/minio "${2:-}"

    build_with_cache docker/storage-init.Dockerfile "$storage_image" . ""
fi
build_with_cache docker/sandbox.Dockerfile "$sandbox_image" . "${3:-}"
