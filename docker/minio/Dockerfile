# MinIO 的官方镜像已不再公开分发：Docker Hub 于 2026-09-11 前后移除 minio/minio 与
# minio/mc，quay.io 上 minio/minio 这个仓库自 2026-09-24 起不再对匿名用户公开（同一
# registry 上其他镜像仍可匿名拉取），dl.min.io 返回 410。此处按官方 GitHub Release
# 发布的二进制重建镜像：版本与 sha256 都取自 release 资产并在构建时校验，运行的是与
# 下架前镜像逐字节相同的 MinIO 二进制（基础镜像与镜像内附带文件不同，见部署文档）。
FROM alpine:3.20

ARG MINIO_VERSION=RELEASE.2023-03-20T20-16-18Z
# 来源：https://github.com/minio/minio/releases/download/<版本>/minio.linux-<arch>.<版本>.sha256sum
ARG MINIO_SHA256_AMD64=df0de9982c4ae440d2c9617bc1da805cf71eb7d9ce5b106b3fdb036fa27ba163
ARG MINIO_SHA256_ARM64=d9c80afe0455f30726457ed7af0af28e1dc8a506ebaf2418ed7d89d763674ff2
ARG TARGETARCH

# curl 供 Compose 的健康检查使用
RUN apk add --no-cache ca-certificates curl

RUN set -eux; \
    arch="${TARGETARCH:-$(apk --print-arch)}"; \
    case "$arch" in \
        amd64|x86_64) sha256="${MINIO_SHA256_AMD64}"; asset="minio.linux-amd64.${MINIO_VERSION}";; \
        arm64|aarch64) sha256="${MINIO_SHA256_ARM64}"; asset="minio.linux-arm64.${MINIO_VERSION}";; \
        *) echo "MinIO 未提供该架构的二进制: ${arch}" >&2; exit 1;; \
    esac; \
    mkdir -p /opt/bin; \
    curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors -o /opt/bin/minio \
        "https://github.com/minio/minio/releases/download/${MINIO_VERSION}/${asset}"; \
    echo "${sha256}  /opt/bin/minio" | sha256sum -c -; \
    chmod +x /opt/bin/minio

# 与原镜像保持一致：二进制位于 /opt/bin，且该目录在 PATH 中
ENV PATH="/opt/bin:${PATH}"

COPY docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/bin/docker-entrypoint.sh

EXPOSE 9000 9001

ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio"]
