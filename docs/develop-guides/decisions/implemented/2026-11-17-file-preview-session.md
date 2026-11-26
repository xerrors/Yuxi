# 文件预览资源归属

状态：implemented
类型：simplification
Owner：frontend/src/modules/workspace/model/filePreviewSession.js

## 问题

SessionWorkspace 同时管理混合面板导航和文件标签、缓存与对象 URL。AgentPanel 也维护缓存淘汰、失效和释放算法。预览资源修改需要同时理解聊天、协作和面板布局，Thread/身份切换后迟到读取可能重新挂入旧资源。

## 决策

### 实现方案

workspace 的 filePreviewSession 拥有文件标签和单一缓存，处理打开、关闭、Thread/身份切换与销毁。filePreviewCache 拥有缓存键、单条/范围释放、淘汰和在途发布。删除先撤销 cache entry 所有权，再释放对象 URL；迟到请求必须仍持有原 loading entry 才能发布，否则回收自身生成的 URL。

AgentPanel 保留实际文件读取、目录刷新和预览展示，通过 workspace 的公开缓存操作管理失效。根工作区通过会话面板状态将文件打开和关闭结果转换为混合面板标签与布局选择。当前面板装配与多来源文件验证见 [会话工作区与 Thread 阅读](2026-11-17-thread-session-reading.md)。

文件标签和缓存以个人空间或来源 Thread 加路径为键。成员文件进入根面板时，预览和下载仍沿用该成员的 Thread 与资源类型。删除文件或目录后，按操作所属 Thread 关闭匹配路径及其子项，保留相邻同前缀文件以及其他作用域的同路径预览。文件内容分别通过个人 workspace、对应 Thread 的 Workdir viewer 或 artifact 接口读取。

## 替代方案

保留页面全部职责不能降低文件修改的上下文。把协作/调试混合面板一起移到 workspace 会让文件域依赖 session。因此只迁移文件资源，由页面组合文件与会话导航。

## 后果

文件生命周期和缓存副作用位于同一领域；Thread 切换释放旧 URL，迟到读取不能重新发布。AgentPanel 仍拥有目录树与预览 IO，不把整个文件界面或整个工作区宣称为完成拆分。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 切换、关闭与销毁清理并拒绝迟到发布 | 标签清空但 URL 留存、旧数据回填 | filePreviewSession / filePreviewCache | `node --test test/unit/filePreviewSession.test.js test/unit/agentPanelFilesystemPolling.test.js test/unit/agentArtifactPreviewRefresh.test.js` | Thread/身份/销毁/关闭后旧请求返回，回收迟到 URL | Passed，23/23 |
| 同名失效和目录关闭范围正确 | 旧产物命中、误删兄弟项 | 同上 | 同上 | 无元数据同名卡片、相邻同前缀、其他 Thread | Passed |
| 页面组合与资源 Owner 不重复 | 两处维护删除和 URL 释放 | Workspace / AgentPanel | 源码搜索、lint、unit、build 和既有浏览器回归 | AgentPanel 无预览缓存释放循环，下载 URL 由下载动作拥有 | Passed |

最终全量 frontend lint/unit（1104/1104）/build 与阅读浏览器回归通过，命令与前端协议覆盖范围见 [Thread 阅读决定](2026-11-17-thread-session-reading.md)。

旧能力不存在：页面与 AgentPanel 不再各自维护预览缓存删除/淘汰/URL 回收算法；session 的目录轮询模块不再拥有预览缓存操作。

重新引入条件：存在不能用当前文件资源 Owner 表达的真实生命周期；不能因调用位置不同复制缓存或 URL 释放逻辑。
