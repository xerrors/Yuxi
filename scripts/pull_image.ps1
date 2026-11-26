param(
    [Parameter(Mandatory=$true)]
    [string]$ImageTag
)

# Docker 的退出码由脚本处理，镜像站失败时仍可回退到原仓库。
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

Write-Host "Pulling image: $ImageTag" -ForegroundColor Green

# 计算斜杠数量来确定镜像格式
$slashCount = ($ImageTag -split '/' | Measure-Object).Count - 1

# 根据镜像格式设置镜像 URL
switch ($slashCount) {
    0 {
        # 无前缀 (例如: python:3.13-slim)
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

docker pull $fullMirrorUrl
if ($LASTEXITCODE -ne 0) {
    Write-Host "Mirror unavailable; pulling from the original registry: $ImageTag" -ForegroundColor Yellow
    docker pull $ImageTag
    exit $LASTEXITCODE
}

docker tag $fullMirrorUrl $ImageTag
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker rmi $fullMirrorUrl
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Process completed successfully!" -ForegroundColor Green
