# PowerShell脚本，用于在Windows系统上打包Docker镜像

# 输出目录与 docker/minio 都相对仓库根；从别处调用时先切过去。
Set-Location (Split-Path -Parent $PSScriptRoot)

# 创建输出目录
$OutputDir = "docker_images_backup"
if (!(Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

# 定义输出文件名
$DateTime = Get-Date -Format "yyyyMMdd"
$OutputFile = "$OutputDir\docker_images_$DateTime.tar"

Write-Host "开始导出Docker镜像到 $OutputFile..." -ForegroundColor Cyan

# 从各个文件中提取的基础镜像列表
$Images = @(
    "python:3.11-slim",
    "ghcr.io/astral-sh/uv:0.12.6",
    "node:24-alpine",
    "node:24-slim",
    "nginx:alpine",
    "neo4j:5.26.29",
    "quay.io/coreos/etcd:v3.5.5",
    "milvusdb/milvus:v2.5.6",
    # "lmsysorg/sglang:v0.4.9.post3-cu126",
    # "ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlex/paddlex:paddlex3.0.1-paddlepaddle3.0.0-gpu-cuda11.8-cudnn8.9-trt8.6"
)

# MinIO 的官方镜像已不再公开分发，改为按仓库内 Dockerfile 构建后再导出。
# 镜像名从 Compose 解析而不是拼装：它跟随 .env 里的 COMPOSE_PROJECT_NAME，与 docker compose up 实际
# 使用的名字一致；硬编码会在用户改过项目名时导出另一个 tag，目标机器上只能现场构建，离线环境直接失败。
docker compose build minio
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ MinIO 镜像构建失败" -ForegroundColor Red
    exit 1
}
$MinioImage = docker compose config --images | Where-Object { $_ -match '-minio:RELEASE' } | Select-Object -First 1
if (-not $MinioImage) {
    Write-Host "❌ 未能从 Compose 配置解析出 MinIO 镜像名" -ForegroundColor Red
    exit 1
}

# 确保所有镜像都已下载
foreach ($Image in $Images) {
    Write-Host "正在拉取镜像: $Image" -ForegroundColor Yellow
    docker pull $Image
}

# 保存所有镜像到单个tar文件
Write-Host "正在保存镜像到tar文件..." -ForegroundColor Yellow
docker save ($Images + $MinioImage) -o $OutputFile

# 计算文件大小
$FileInfo = Get-Item $OutputFile
$FileSizeMB = [math]::Round($FileInfo.Length / 1MB, 2)
$FileSizeGB = [math]::Round($FileInfo.Length / 1GB, 2)

Write-Host "完成！" -ForegroundColor Green
Write-Host "所有Docker镜像已保存到: $OutputFile" -ForegroundColor Green
if ($FileSizeGB -ge 1) {
    Write-Host "文件大小: $FileSizeGB GB" -ForegroundColor Green
} else {
    Write-Host "文件大小: $FileSizeMB MB" -ForegroundColor Green
}

Write-Host "`n要在另一台机器上加载这些镜像，请使用命令:" -ForegroundColor Cyan
Write-Host "docker load -i $OutputFile" -ForegroundColor White
