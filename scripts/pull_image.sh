#!/bin/bash

if [ $# -ne 1 ]; then
    echo "Usage: $0 <image:tag>"
    exit 1
fi

set -e  # 当命令失败时，立即退出脚本

IMAGE_TAG=$1

# Count the number of slashes to determine the image format
SLASH_COUNT=$(echo $IMAGE_TAG | tr -cd '/' | wc -c)

# Set mirror URL based on image format
if [ $SLASH_COUNT -eq 0 ]; then
    # No prefix (e.g., python:3.12-slim)
    MIRROR_URL="m.daocloud.io/docker.io/library"
elif [ $SLASH_COUNT -eq 1 ]; then
    # One prefix (e.g., milvusdb/milvus:latest)
    MIRROR_URL="m.daocloud.io/docker.io"
else
    # Two or more prefixes (e.g., quay.io/coreos/etcd:v3.5.5)
    MIRROR_URL="m.daocloud.io"
fi

# Pull image from mirror
echo "Pulling image from mirror: $MIRROR_URL/$IMAGE_TAG"
docker pull $MIRROR_URL/$IMAGE_TAG

# Tag image with original name
docker tag $MIRROR_URL/$IMAGE_TAG $IMAGE_TAG

# Remove mirror image
docker rmi $MIRROR_URL/$IMAGE_TAG

docker images

echo "Process completed successfully!"