# 镜像准备命令按实际结果退出

状态：implemented
类型：bug-fix
Owner：scripts/pull_image.ps1

## 问题

Windows 脚本中 Docker 的非零退出码可以绕过 PowerShell 异常处理。镜像拉取和打包失败后，脚本可能继续执行并显示成功。CI 拓扑构建器也可能把 Compose 中缺失的镜像名解释为字符串 `null`。

## 决策

### 实现方案

镜像拉取按镜像站、原仓库的顺序尝试。PowerShell 脚本检查 Docker 的退出码，镜像站拉取成功时再标记原名称。归档入口从 Compose 按服务读取生成镜像，Docker `save` 成功后才显示当前归档的位置与大小。允许已有本地镜像在远端拉取失败时参与归档，由 `docker save` 核对镜像是否齐全。

CI 拓扑构建入口一次读取 Compose JSON，在调用 Docker 构建前验证本次模式需要的镜像名。完整拓扑要求 API、MinIO、storage-init 和沙盒镜像，沙盒模式只要求沙盒镜像。

## 替代方案

把所有命令统一设置为异常即退出会跳过镜像站失败后的原仓库尝试，也会阻止使用已有本地镜像。各阶段依据其是否可以继续来处理 Docker 的结果。

## 后果

镜像归档失败可以通过退出码识别，已有归档的存在不会替代本次 `docker save` 的结果。CI 在开始构建前报告缺失的镜像配置。

## 验证

工程信任工作流执行 `pwsh -NoProfile -File scripts/ci/test_docker_image_scripts.ps1` 与 `python3 -m unittest scripts.ci.test_resource_boundaries`。测试通过可控制的 Docker 替身验证实际标签、归档清单、退出码，以及缺少镜像时没有开始构建。
