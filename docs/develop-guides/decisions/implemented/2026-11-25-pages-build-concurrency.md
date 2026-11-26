# Pages 发布并发与产物上传边界

状态：implemented
类型：bug-fix
Owner：.github/workflows/deploy.yml

## 问题

文档 workflow 可以在分支、标签和手动触发时构建站点，只有推送到 `main` 会部署。GitHub Actions 同一并发组只保留一个等待中的运行，后到的构建可以替换队列中的 `main` 发布。每次构建都上传 Pages 产物也会为非发布检查产生无消费者的产物。

## 决策

### 实现方案

`deploy.yml` 按触发事件和 Git ref 分组，同一 ref 的手动构建与推送构建各自排队。`main` 推送继续串行发布，正在进行的部署可以完成。`docs-check.yml` 在各调用路径构建文档，Pages 产物上传和部署均要求推送到 `refs/heads/main`。

## 替代方案

只给部署 job 设置并发组也可保护发布，但同一分支的重复构建会并发消耗资源。按事件与 ref 分组保留重复构建的收敛方式。通过共享工作流参数传递是否上传会新增调用方契约，现有调用路径已经提供事件与 ref。

## 后果

PR、非主分支推送、版本标签和手动触发均可构建文档。不同并发组可以同时运行。GitHub Actions 的并发组名称忽略大小写，同一事件下仅大小写不同的 ref 仍可能共用等待队列。

## 验证

`python3 -m unittest scripts.ci.test_release_workflows` 核对并发配置包含触发事件与 Git ref，并验证 PR、非主分支推送、版本标签、手动触发和 `main` 推送五类事件的上传与部署条件。恢复共享并发组或无条件上传后，对应测试失败。远端排队、上传和部署超出本地验证范围。
