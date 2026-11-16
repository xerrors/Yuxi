#!/bin/sh
set -eu

# API 数据根只调整挂载根；Milvus 数据树需要恢复历史目录的运行用户。
chown 1000:1000 /app/user-data /app/skill-sources /app/skill-projections
chmod 0700 /app/user-data /app/skill-sources /app/skill-projections
# 保留已有文件内容和模式，仅递归修复 Milvus 运行所需的属主。
chown -R 999:999 /var/lib/milvus /var/lib/milvus/logs
chmod 0755 /var/lib/milvus /var/lib/milvus/logs
