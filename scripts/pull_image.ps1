param(
    [Parameter(Mandatory=$true)]
    [string]$ImageTag
)

# 当命令失败时，立即退出脚本
$ErrorActionPreference = "Stop"

Write-Host "Pulling image: $ImageTag" -ForegroundColor Green

# 计算斜杠数量来确定镜像格式
$slashCount = ($ImageTag -split '/' | Measure-Object).Count - 1

# 根据镜像格式设置镜像 URL
switch ($slashCount) {
    0 {
        # 无前缀 (例如: python:3.12-slim)
        $mirrorUrl = "m.daocloud.io/docker.io/library"
        Write-Host "Image format: Official image (no prefix)" -ForegroundColor Cyan
    }
    1 {
        # 一个前缀 (例如: milvusdb/milvus:latest)
        $mirrorUrl = "m.daocloud.io/docker.io"
        Write-Host "Image format: Hub repository (one prefix)" -ForegroundColor Cyan
    }
    default {
        # 两个或更多前缀 (例如: quay.io/coreos/etcd:v3.5.5)
        $mirrorUrl = "m.daocloud.io"
        Write-Host "Image format: Third-party registry (multiple prefixes)" -ForegroundColor Cyan
    }
}

$fullMirrorUrl = "$mirrorUrl/$ImageTag"
Write-Host "Mirror URL: $fullMirrorUrl" -ForegroundColor Yellow

try {
    # 从镜像加速器拉取镜像
    Write-Host "Step 1: Pulling image from mirror..." -ForegroundColor Blue
    docker pull $fullMirrorUrl

    # 重新标记为原始名称
    Write-Host "Step 2: Tagging image with original name..." -ForegroundColor Blue
    docker tag $fullMirrorUrl $ImageTag

    # 删除镜像加速器标签
    Write-Host "Step 3: Removing mirror tag..." -ForegroundColor Blue
    docker rmi $fullMirrorUrl

    Write-Host "`nProcess completed successfully!" -ForegroundColor Green
    Write-Host "`nCurrent Docker images:" -ForegroundColor Yellow
    docker images

} catch {
    Write-Host "`nError occurred: $_" -ForegroundColor Red
    exit 1
}