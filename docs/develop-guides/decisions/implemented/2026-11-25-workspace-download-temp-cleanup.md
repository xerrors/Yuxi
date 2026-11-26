# 下载临时文件随复制和响应生命周期回收

状态：implemented
类型：bug-fix
Owner：backend/yuxi/api/responses/files.py

## 问题

Viewer 在复制文件时收到取消，可能留下准备中的副本。已进入 HTTP 响应的副本也可能在拒绝 Range 或发送中断时留在磁盘，因为这些路径可以提前结束响应。

## 决策

### 实现方案

Viewer 服务在取消时等待已开始的复制退出，再删除临时副本并传播取消。复制成功后，HTTP 文件响应负责回收副本。响应复用 `FileResponse` 的协议实现，在响应退出时删除 `PreparedFile` 所指向的临时内容，包括无效 Range 和传输中断产生的早退。

## 替代方案

只处理响应状态 `400` 和 `416` 会遗漏发送失败。定期扫描临时目录需要另行判断哪些副本仍在使用。复制和响应各自在所有权结束时删除副本。

## 后果

下载准备时的取消需要等待本次复制停止。HTTP 响应保留原有文件名和 Range 行为，源文件继续由 Workdir 拥有。

## 验证

`test_viewer_filesystem_service.py` 取消进行中的复制并回读临时目录。`test_prepared_file_response.py` 验证响应已读出文件时的发送失败与取消，回读临时文件清理结果。`test_viewer_filesystem_router.py` 的 Range 回归通过真实 HTTP 核对响应内容、文件名、字节范围和磁盘结果，独立服务集成任务执行该用例。恢复原清理逻辑会在取消、发送失败或拒绝 Range 后留下副本。
