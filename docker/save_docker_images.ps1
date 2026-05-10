# PowerShell脚本，用于在Windows系统上打包Docker镜像

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
    "ghcr.io/astral-sh/uv:0.7.2",
    "node:24-alpine",
    "node:24-slim",
    "nginx:alpine",
    "neo4j:5.26",
    "quay.io/coreos/etcd:v3.5.5",
    "minio/minio:RELEASE.2023-03-20T20-16-18Z",
    "milvusdb/milvus:v2.5.6",
    # "lmsysorg/sglang:v0.4.9.post3-cu126",
    # "ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlex/paddlex:paddlex3.0.1-paddlepaddle3.0.0-gpu-cuda11.8-cudnn8.9-trt8.6"
)

# 确保所有镜像都已下载
foreach ($Image in $Images) {
    Write-Host "正在拉取镜像: $Image" -ForegroundColor Yellow
    docker pull $Image
}

# 保存所有镜像到单个tar文件
Write-Host "正在保存镜像到tar文件..." -ForegroundColor Yellow
docker save $Images -o $OutputFile

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