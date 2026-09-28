# 确定性 E2E 的沙盒镜像准备

状态：implemented
类型：testing
Owner：.github/workflows/system-tests.yml

## 问题

Runtime System Tests 的冷 runner 只预先构建 API、provisioner 和 MinIO 镜像。首次 execute 创建沙盒时，Docker SDK 在镜像不存在的情况下同步拉取镜像，下载时间计入 E2E 的 240 秒 Run 预算。审批恢复测试在 main 与多图 PR 上均出现等待 SSE 超时，清理后执行请求返回 sandbox not found。

## 决策

focused runtime job 在启动拓扑前，读取 Compose 解析后的 SANDBOX_IMAGE 并执行 docker pull。下载失败归属环境准备步骤。Run 超时、断言、测试选择器和真实沙盒调用保持不变。

## 替代方案

- 延长 Run 超时：混淆环境下载与执行耗时，不采用。
- 跳过 execute 或改为 mock：无法验证审批恢复后的真实工具审计与落盘，不采用。
- 在 workflow 硬编码镜像：与 Compose 事实源重复，不采用。

## 后果

首次镜像下载仍需时间，但位于独立可诊断步骤。该改动只影响 CI；shipping runtime 继续惰性创建沙盒。

## 验证

Docker SDK 的 ImageNotFound 分支确实执行同步 images.pull；CI 使用解析后的 Compose 镜像。原失败用例仍由同一 workflow 执行，最终结果以该提交实际 CI 为准，镜像预拉取本身不证明业务正确。
