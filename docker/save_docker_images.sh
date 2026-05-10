#!/bin/bash

# 创建输出目录
OUTPUT_DIR="docker_images_backup"
mkdir -p $OUTPUT_DIR

# 定义输出文件名
OUTPUT_FILE="$OUTPUT_DIR/docker_images_$(date +%Y%m%d).tar"

echo "开始导出 Docker 镜像到 $OUTPUT_FILE..."

# 从各个文件中提取的基础镜像列表
IMAGES=(
    "python:3.12-slim",
    "ghcr.io/astral-sh/uv:0.7.2",
    "node:24-alpine",
    "node:24-slim",
    "nginx:alpine",
    "neo4j:5.26",
    "quay.io/coreos/etcd:v3.5.5",
    "minio/minio:RELEASE.2023-03-20T20-16-18Z",
    "milvusdb/milvus:v2.5.6",
)

# 确保所有镜像都已下载
for IMAGE in "${IMAGES[@]}"; do
    echo "正在拉取镜像: $IMAGE"
    docker pull $IMAGE
done

# 保存所有镜像到单个 tar 文件
echo "正在保存镜像到 tar 文件..."
docker save ${IMAGES[@]} -o $OUTPUT_FILE

# 计算文件大小
FILE_SIZE=$(du -h $OUTPUT_FILE | cut -f1)

echo "完成！"
echo "所有 Docker 镜像已保存到: $OUTPUT_FILE"
echo "文件大小: $FILE_SIZE"
echo "使用命令: docker load -i $OUTPUT_FILE 加载镜像"