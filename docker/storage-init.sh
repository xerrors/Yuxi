#!/bin/sh
set -eu

# 初始化挂载根，不遍历用户文件或修改索引内部权限。
chown 1000:1000 /app/user-data /app/skill-sources /app/skill-projections
chmod 0700 /app/user-data /app/skill-sources /app/skill-projections
chown 999:999 /var/lib/milvus /var/lib/milvus/logs
chmod 0755 /var/lib/milvus /var/lib/milvus/logs

# 用量标记由 API、worker 和 root runner 共用，且不挂载到用户沙盒。
mkdir -p /app/user-data/.workspace-usage/locks
chown 1000:1000 /app/user-data/.workspace-usage /app/user-data/.workspace-usage/locks
chmod 0700 /app/user-data/.workspace-usage /app/user-data/.workspace-usage/locks
