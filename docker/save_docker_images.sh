#!/bin/bash
set -euo pipefail

# 输出目录与 docker/minio 都相对仓库根；从别处调用时先切过去。
cd "$(dirname "$0")/.."

# 创建输出目录
OUTPUT_DIR="docker_images_backup"
mkdir -p $OUTPUT_DIR

# 定义输出文件名
OUTPUT_FILE="$OUTPUT_DIR/docker_images_$(date +%Y%m%d).tar"

echo "开始导出 Docker 镜像到 $OUTPUT_FILE..."

# 从各个文件中提取的基础镜像列表
IMAGES=(
    "python:3.13-slim",
    "ghcr.io/astral-sh/uv:0.12.6",
    "node:24-alpine",
    "node:24-slim",
    "nginx:alpine",
    "neo4j:5.26.29",
    "quay.io/coreos/etcd:v3.5.5",
    "milvusdb/milvus:v2.5.6",
)

# MinIO 的官方镜像已不再公开分发，改为按仓库内 Dockerfile 构建后再导出。
# 镜像名从 Compose 解析而不是拼装：它跟随 .env 里的 COMPOSE_PROJECT_NAME，与 docker compose up 实际
# 使用的名字一致；硬编码会在用户改过项目名时导出另一个 tag，目标机器上只能现场构建，离线环境直接失败。
docker compose build minio
MINIO_IMAGE=$(docker compose config --images | grep -- '-minio:RELEASE') || {
    echo "❌ 未能从 Compose 配置解析出 MinIO 镜像名（需要可用的 .env）"
    exit 1
}

# 确保所有基础镜像都已下载。拉取失败不中止：导出机常已通过 scripts/pull_image.sh 的镜像源备好镜像，
# 此时直连 Docker Hub 会失败，而能否导出由最后的 docker save 把关。
for IMAGE in "${IMAGES[@]}"; do
    echo "正在拉取镜像: $IMAGE"
    docker pull "$IMAGE" || echo "⚠️ ${IMAGE} 拉取失败，继续使用本地镜像"
done

# 保存所有镜像到单个 tar 文件
echo "正在保存镜像到 tar 文件..."
docker save ${IMAGES[@]} "$MINIO_IMAGE" -o $OUTPUT_FILE

# 计算文件大小
FILE_SIZE=$(du -h $OUTPUT_FILE | cut -f1)

echo "完成！"
echo "所有 Docker 镜像已保存到: $OUTPUT_FILE"
echo "文件大小: $FILE_SIZE"
echo "使用命令: docker load -i $OUTPUT_FILE 加载镜像"
