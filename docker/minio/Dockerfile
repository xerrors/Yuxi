# 使用官方 GitHub Release 的固定版本二进制，按目标架构校验 SHA-256。
FROM alpine:3.20

ARG MINIO_VERSION=RELEASE.2024-12-18T13-15-44Z
# 来源：https://github.com/minio/minio/releases/download/<版本>/minio.linux-<arch>.<版本>.sha256sum
ARG MINIO_SHA256_AMD64=88182336ab5793488f2caad54846056b330f0dcb5c488b60d52434beeebae3ca
ARG MINIO_SHA256_ARM64=455c9b3093b7c1e6153c85cfd745c7c401a92497346ba24dbe45775c7b3ab1cd
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

COPY --chown=0:0 --chmod=0755 docker-entrypoint.sh /usr/bin/docker-entrypoint.sh

EXPOSE 9000 9001

ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio"]
