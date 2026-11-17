# 角色、共享资源与私有 Agent

本页面向平台管理员和开发者，解释账号、资源定义与执行身份的授权边界。Agent 字段参考见[配置智能体](../agents/agents-config.md)，外部调用身份见 [API Key 接入](../advanced/api-key-integration.md)。

## 角色与账号

角色决定建设能力，资源授权决定具体对象的使用和管理能力。`user` 可以使用获授权资源并管理自己的私有 Agent；`admin` 可以建设和维护获授权的共享 Agent、知识库和 Skill；`superadmin` 管理系统配置、部门、角色、内置定义及平台共享资源。

系统管理员可以提升角色，允许 `user → admin`、`user → superadmin` 和 `admin → superadmin`。角色写入由数据库约束禁止降级，角色提升保留资源的私有或共享状态。多个系统管理员可以共存。删除使用共同事务锁和数据库触发器，至少保留一个未删除的系统管理员。

部门管理员只能创建、修改基本资料和删除本部门普通成员。初始密码在创建时设置，已有成员密码、角色和部门归属由系统管理员管理。部门成员管理权只作用于账号；私有 Agent、会话和文件各自执行资源隔离。成员操作在获得事务锁后重新读取操作者和目标账号，等待期间失效的权限不会沿用。平台不提供模拟其他用户登录的入口。删除账号保留其资源定义，共享定义由其他获授权管理员维护，私有定义由系统管理员治理。

用户管理 service 拥有资料、头像引用、成员创建、修改与删除的提交点。账号删除在同一事务撤销 API Key 并调用 schedules 仓储删除任务及调度历史。后续清理失败时回滚账号和凭据。部门变更由组织管理 service 提交。部门删除迁移成员并撤销绑定该部门的凭据。用户偏好保存通过唯一 UID 的原子 upsert 完成，仓储写入不提交调用方事务。

## 共享资源

共享 Agent、知识库和 Skill 使用 version 2 的 `read_scope` 与 `manage_scope`。读取包含使用，管理包含编辑、删除和共享配置写入。普通用户的权限上限为读取；管理员所有者和系统管理员隐含拥有管理权，其他管理员需要同时命中读取与管理范围。全员或部门管理范围只对管理员生效，指定管理用户必须为有效管理员。

| 读取范围 | 可保存的管理范围 |
| --- | --- |
| 全员 | 全员、指定部门、指定管理员 |
| 指定部门 | 同一组部门的子集 |
| 指定用户 | 同一组用户中的管理员子集 |
| 空 | 空 |

获授权管理员可以向其他部门或全平台授权。所有权转移使用系统管理员专用接口，接收者必须为有效管理员；私有或内置定义拒绝转移。共享 MCP 的连接、单工具启停、模型供应商凭据和全局配置由系统管理员维护，其他角色使用平台开放的能力。

## 私有、发布与治理

Agent 的 `visibility` 持久化为 `private` 或 `shared`，创建默认私有。私有 Agent 由所有者正常使用、编辑和删除；其他用户和管理员无法列出、读取或运行。系统管理员可以治理其他人的私有定义，但运行和转移仍要求符合各自边界。内置定义为共享状态，内置维护仅允许系统管理员。

管理员可显式创建共享 Agent。有管理权限的管理员也可通过同一更新接口将私有定义的 `visibility` 改为 `shared`，同时提交明确的 `share_config`。Agent repository 在锁定定义和有效操作者后校验管理权、角色和授权范围，更新同一条记录；普通用户、无授权范围的转换与共享转私有均拒绝。Agent definitions service 在同一事务中校验专属 Skill 的依赖权限并刷新投影，依赖范围不匹配时回滚定义更新。原 ID、slug、所有者和 Context 保留，会话、附件、工作目录、运行产物和个人 Skill 始终属于实际调用者。

共享 Agent 的使用者可以读取提示词与配置。配置中的字段角色限制仍在写入时执行，越权字段提交明确拒绝。所有 APP 终端用户，包括默认终端用户，只能发现和使用获授权的共享 Agent；个人 JWT 和未切换 APP 身份的个人 API 可以访问自己的私有 Agent。

## 执行、撤权与结果

HTTP 查询、worker 准备、定时触发、等待恢复、模型和工具边界使用当前调用者。模型重试每次重新授权；摘要模型同样复查权限，主动压缩没有 Run 时通过会话归属找到当前 Agent。Agent 所有者的身份只表达定义归属。APP 运行使用终端用户身份，共享 Agent 可见性按 Key 所属账号的 UID 和部门匹配显式读取范围；该匹配将角色按普通用户处理，不借用所有者或系统管理员的隐含治理权限，知识库和 Skill 依赖按实际终端用户授权解析，管理员建设能力不会传递给终端用户。

无权依赖从本次 Context 过滤，显式 `all` 按当前可用资源展开，已保存配置保留原引用。事件 `yuxi.session.turn.capability_limited` 提供通用提示，不包含无权资源名称或内容。共享 Skill 按当前用户刷新投影，个人 Skill 来自调用者自己的目录。Agent 专属 Skill 从绑定 Agent 派生权限；APP 读取复用 Agent 的显式可见性且角色上限为读取，其普通 Skill 依赖继续按实际终端用户授权解析。实际工具调用复查资源授权、Skill 激活状态以及 MCP 服务器和单工具启用状态；协作会话在统一执行与工具边界再次授权。

账号失效或 Agent 使用权撤销使后续执行形成失败终态，Run 保存错误，Turn 投影对应结果。仅依赖失权时拒绝该依赖的新访问，其他能力继续执行。已交给外部服务的动作和已进入上下文的信息不具有撤回承诺。历史会话与产物保留，并沿其所属资源和文件边界读取。

## 源码定位与验证

- [权限解析](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/identity/permissions/resource_permission.py)拥有角色上限、范围关系和私有可见性；[Agent repository](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/repositories/definitions.py)拥有定义写入和共享定义授权事务。
- [用户用例](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/identity/services/users.py)拥有成员与资料事务；[系统资源治理](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/system/services/resource_ownership.py)编排跨域所有权转移，Agent 与 Skill 保留各自的投影发布入口。
- [身份 schema](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/migrations/schema.py)拥有角色与有效系统管理员约束；[授权中间件](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/middlewares/authorization.py)拥有模型和工具执行边界。
- [HTTP 验收](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_permission_convergence.py)回读定义与账号；[身份并发测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/identity/test_identity_permission_constraints.py)使用两个 PostgreSQL 连接；[撤权 E2E](https://github.com/xerrors/Yuxi/blob/main/backend/test/e2e/test_permission_revocation_e2e.py)回读运行终态、工具结果和文件。

权限持久状态使用全新 schema 初始化。部署和隔离环境选择见[并行工作树与隔离运行环境](../develop-guides/parallel-worktree-environments.md)。测试命令由[测试规范](../develop-guides/testing-guidelines.md)维护。
