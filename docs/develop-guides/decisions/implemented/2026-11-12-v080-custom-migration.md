# v0.8 架构下保留本地定制能力

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/knowledge/manager.py

## 问题

v0.8 使用统一后端包、领域模块、Input/Turn/Run、代次化知识索引和独立沙盒 runner。本地版本同时拥有父子分块、BGE-M3 原生稀疏向量与融合检索、显式重切片、独立功能导航、WPS 授权与工作区空间入口。目录重命名不能直接证明这些能力仍经新装配提供。

## 决策

### 实现方案

后端沿用 `backend/yuxi` 的领域服务与 repository，前端沿用 `frontend` 的模块入口和会话导航。知识库定制由 knowledge 与 models 拥有，复用新文件代次、投影锁和后台作业；重切片复用索引执行器，并保留同意图去重、文件重叠拒绝和提交后派发。WPS 的配置、用户连接与凭据由 identity 拥有，HTTP 路由注册在 API，隧道仅由 API 进程按需管理。

Compose 保留新架构的初始化与 runner 拓扑，同时恢复本地模型只读挂载和 API 专用隧道配置。既有数据库、对象与宿主模型文件不由合并过程修改；旧 Schema 与新运行时之间的兼容性必须单独验证，版本号不通过改写数据库标记伪装兼容。合并现场和本地提交均保留可恢复备份，合并结果停留在未提交状态。

## 替代方案

- 保留旧运行架构，仅合入兼容修复：减少迁移工作，但不满足用户选择的 v0.8 架构升级。
- 全部采用 v0.8 内容：会丢失当前使用的本地定制能力。
- 同时运行两套业务实现：增加状态与配置 Owner，无法证明新入口实际调用了本地能力。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 新目录装配完整且没有未合并路径 | 旧导入与重复目录绕过新实现 | API、模块入口、Compose | 工程契约 gate、定向 import 检查、git ls-files -u | 旧路径导入或缺失注册导致失败 | 工程契约 gate 通过；未合并索引为空；显式冲突标记扫描尚未作为独立检查完成 |
| 父子分块与融合检索保留 | 只有配置展示而无执行消费 | knowledge、models | 知识库 unit、PostgreSQL 边界测试、真实检索组件测试 | 关闭 sparse、非法模型和跨代次数据 | Passed：107 passed；独立 PG 投影边界 7 passed；2 项因离线缺少 tiktoken 词表跳过 |
| WPS 用户隔离与授权契约保留 | 凭据泄漏、状态重放或未注册路由 | identity、API | 协议、隧道 unit 与 HTTP/PG integration | 错误 state、他人资源和无权限配置 | Passed：相关后端 82 项；WPS HTTP/PG 10 项；真实 WPS 与 Cloudflare 未在线验收 |
| 导航、空间入口及知识配置可用 | 页面存在但调用旧模块 | frontend | lint、unit、build 与页面检查 | 旧目录导入及丢失入口 | Lint/build 通过；前端全量汇总曾为 1078/1078 通过。最终定向日志另外记录 workspaceDirectoryDialog 14/15，其中 1 项 renderer 测试失败，尚未复现确认 |
| Schema 从标准 v0.8 受控升级 | 静默接受未知/定制旧表或伪造版本 | app-init、migration | 隔离 PostgreSQL 显式扩展与负向案例 | 非标准版本、未知表和非预期源版本拒绝 | Passed：显式扩展 3 项；数据库仅使用临时隔离实例 |
| 当前工作未提交且持久数据未改动 | 误提交或误启动迁移 | Git、部署入口 | HEAD/MERGE_HEAD、索引、冲突索引和执行命令核对 | HEAD 移动或真实数据库写入 | Passed：保持合并态与暂存态；没有操作业务数据 |

## 后果

本地旧 Windows venv 缺少新架构依赖与 Unix 文件系统能力，不能作为新实现的完整后端测试环境。旧 Linux 镜像全量后端单元测试在收集阶段因镜像未安装当前锁文件中的 `cubesandbox==0.7.3` 而中断（7 个 collection errors，未执行测试）；该镜像此前另有一轮 3782 passed、129 skipped、39 failed、12 errors 的旧依赖结果，不能作为当前全量通过证据。当前容器引擎不可连接，无法用该镜像重跑定向套件；此前相关后端 82 项定向测试与知识库 107 项回归通过。真实 WPS 服务、Cloudflare Tunnel、浏览器交互和本地 BGE-M3 模型推理未在线验证。部署 Schema 扩展只在临时隔离 PostgreSQL 中验证；未迁移既有数据库、对象、向量或用户文件。
