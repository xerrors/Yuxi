#!/bin/sh
# 与原镜像的 /usr/bin/docker-entrypoint.sh 保持同一语义：命令首项不是 minio 时自动前置，
# 因此 `command: minio server ...` 与 `docker run <镜像> server ...` 都能工作。
# 原脚本另支持用 MINIO_USERNAME/MINIO_GROUPNAME 切换运行用户；仓库两份 Compose 都未使用，
# 故不引入 useradd/setpriv 依赖。
if [ "${1}" != "minio" ] && [ -n "${1}" ]; then
    set -- minio "$@"
fi

exec "$@"
