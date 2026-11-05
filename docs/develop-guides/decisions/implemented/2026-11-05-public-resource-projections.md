# 公开资源的错误、媒体识别与临时文件生命周期

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/services/artifacts.py

## 问题

普通会话的公开错误可包含解释器对象地址或 traceback。无准确扩展名的音视频产物无法按内容识别。产物复制在线程中执行，协程取消后线程仍可能写入临时文件，导致提前清理无效或文件泄漏。这些问题由普通会话入口即可触发。

## 决策

### 实现方案

公开 Turn 和 Run 投影共用错误文本解释：含解释器对象转储或 traceback 的文本返回稳定失败说明，其他业务及提供者文本保持原样，数据库原始错误不改写。文件媒体识别继续由 `shared/files.py` 拥有，在既有签名识别中补音视频，无法识别时保留文件名规则；签名识别不承担完整格式验证。

产物用例复用既有 `await_io` 等待下载线程结束，再传播取消并删除临时文件。下载、预览和保存的源文件复制共用这个边界。保存后半段的 Workspace 上传不在此决定的取消保证内。不增加助理交付描述 API、依赖、配置或数据迁移。

## 替代方案

- 只在助理页面处理：普通会话与 Public API 仍暴露相同缺陷。
- 隐藏所有错误：会丢失有意提供的业务错误信息。
- 只改扩展名：无法处理无扩展名或错误扩展名的文件。
- 取消后立即删除：后台线程仍可能继续写入或重新创建目标文件。

## 后果

错误解释仅覆盖明确的对象转储和 traceback，不宣称完整脱敏。媒体签名为有限格式启发式。取消可能等待底层同步 I/O 结束，不扩展为整个保存流程的原子取消承诺。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 公开错误隐藏对象转储并保留业务文本及原始记录 | 只处理一个投影或误隐藏业务错误 | public_resources、public_items | unit + 真实 HTTP 投影及 PostgreSQL 原始错误回读 | 历史失败、普通业务错误 | Passed |
| 音视频签名决定响应类型，未知内容保留后缀规则 | 工具测试通过但 HTTP 响应类型错误 | shared/files、artifacts | MIME unit + artifact 真实 HTTP 响应字节与类型回读 | 错误扩展名、未知文件 | Passed |
| 取消源文件复制后不残留或晚写临时文件 | 协程退出但线程未退出 | artifacts、await_io | 阻塞真实复制线程后取消、释放并回读文件 | 下载、预览、保存复制阶段 | Passed |

定向单测 `test/unit/services/test_public_run_error.py`、`test/unit/services/test_artifact_service.py`、`test/unit/utils/test_shared_files.py` 共 77 项通过。真实 HTTP 用例 `test/integration/api/test_public_session_resources.py::test_public_error_and_media_projections_preserve_stored_resources` 通过：错误投影不更改持久原文，错误后缀文件的响应保留音频字节并返回 `audio/wav`。旧实现的历史 Run 错误断言、音频类型及三个复制阶段取消断言均因目标缺陷失败；运行旧部署时真实 HTTP 音频类型断言也失败。

取消证据覆盖复制线程与临时文件的实际生命周期，不宣称验证了 Workspace 上传阶段的取消。
