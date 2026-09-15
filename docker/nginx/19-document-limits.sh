#!/bin/sh
set -eu
# 与 API 共用部署硬上限；留 1 MiB 给 multipart 元数据。
case "${DOCUMENT_UPLOAD_HARD_MAX_MIB:-100}" in
    ''|*[!0-9]*) echo "Invalid DOCUMENT_UPLOAD_HARD_MAX_MIB" >&2; exit 1 ;;
esac
cap=${DOCUMENT_UPLOAD_HARD_MAX_MIB:-100}
[ "$cap" -ge 1 ] && [ "$cap" -le 1048576 ] || exit 1
export DOCUMENT_REQUEST_MAX_MIB=$((cap + 1))
# entrypoint 的后续脚本运行在其他 shell，直接限定变量渲染，保留 nginx 的 $uri 等变量。
envsubst '${DOCUMENT_REQUEST_MAX_MIB}' < /etc/nginx/document-limits.conf.template > /etc/nginx/conf.d/default.conf
