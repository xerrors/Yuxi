# Yuxi Sandbox 技术设计文档

## 文档目标

这份文档描述的是 **Yuxi 当前已经落地的沙盒系统设计**，不是一个理想化方案，也不是 API 参考手册。

::: warning 预发布
此部分所涉及到的技术路线和方案文档，均可能会随时改变。
:::


重点回答五个问题：

1. 当前沙盒到底解决了什么问题
2. 当前沙盒在系统里是怎么接入的
3. Agent、工作台文件浏览器、Skills 与沙盒之间的关系是什么
4. 线程级隔离、路径隔离、容器生命周期是如何实现的
5. 当前方案的边界、局限性和后续演进方向是什么

如果只想快速建立整体认识，建议先看：

- 第 1 节：设计结论
- 第 2 节：系统边界
- 第 3 节：整体架构
- 第 5 节：生命周期
- 第 11 节：当前局限性

---

## 1. 设计结论

当前 Yuxi 的沙盒不是“泛化的安全执行平台”，而是一套 **围绕线程工作区、Agent 文件工具、工作台文件浏览器** 设计的受控执行环境。

它的核心特征可以概括为：

- **线程级隔离**：资源归属从早期的 user 级沙盒收敛为 `thread_id -> sandbox_id`
- **容器级执行**：默认用本机 Docker 容器承载执行环境
- **命名空间约束**：Agent 和前端主要通过 `/mnt/user-data` 与 `/mnt/skills` 看到文件系统
- **惰性初始化**：不再依赖公开的 `/api/sandbox/*` 生命周期接口；首次访问时自动获取沙盒
- **双视图设计**：
  - Agent 工具侧使用 composite backend，兼顾技能只读路由
  - 工作台侧使用 viewer-oriented filesystem service，强调真实目录浏览和原始文件内容

这意味着它更接近：

- “给 Agent 提供一个线程隔离的工作目录和有限 shell 执行能力”

而不是：

- “一个对外承诺强安全边界的多租户通用沙箱平台”

这个定位非常重要。后续阅读整份文档时，所有设计取舍都建立在这个现实目标之上。

---

## 2. 系统边界

### 2.1 当前沙盒负责什么

当前沙盒主要负责以下能力：

- 为某个 thread 提供独立的用户数据目录
- 在 Docker 容器中执行 Agent 发起的命令
- 为 Agent 文件工具提供受限文件系统
- 为工作台文件浏览器提供可视化文件访问入口
- 将 skills 以只读目录暴露给 Agent 和工作台

### 2.2 当前沙盒不负责什么

当前实现并不试图完整解决以下问题：

- 强多租户对抗场景下的高强度安全隔离
- 资源配额的精细控制
- 审计级别的命令执行记录
- 持久化的沙盒元数据控制平面
- 面向通用用户的文件编辑、删除、重命名、上传管理 API

这不是缺陷描述，而是范围定义。文档后面提到的“限制”，也应放在这个范围下理解。

---

## 3. 整体架构

### 3.1 架构分层

当前沙盒相关实现大致分为五层：

1. **Provisioning / Runtime Backend 层**
   - [local_container_backend.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/local_container_backend.py)
   - [remote_sandbox_backend.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/remote_sandbox_backend.py)
2. **Provider 层**
   - [sandbox_provider.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/sandbox_provider.py)
3. **单个沙盒文件/执行后端层**
   - [sandbox_backend.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/sandbox_backend.py)
4. **Agent / Viewer 接入层**
   - [composite.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/composite.py)
   - [filesystem_service.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/services/filesystem_service.py)
   - [viewer_filesystem_service.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/services/viewer_filesystem_service.py)
5. **HTTP Router / UI 使用层**
   - [filesystem_router.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/server/routers/filesystem_router.py)
   - [viewer_filesystem_router.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/server/routers/viewer_filesystem_router.py)
   - [AgentPanel.vue](/Users/wenjie/Documents/projects/Yuxi-Know/web/src/components/AgentPanel.vue)

### 3.2 一张图看清调用关系

```text
Agent / Frontend
    |
    +-- Agent filesystem tool
    |       |
    |       +-- create_agent_composite_backend(...)
    |               |
    |               +-- default: YuxiSandboxBackend
    |               +-- route /mnt/skills/: SelectedSkillsReadonlyBackend
    |
    +-- Workbench viewer panel
            |
            +-- /api/v1/viewer/filesystem/*
                    |
                    +-- viewer_filesystem_service
                            |
                            +-- thread ownership check
                            +-- resolve agent config / visible skills
                            +-- provider.acquire(thread_id)
                            +-- sandbox backend or skills backend

Provider
    |
    +-- LocalContainerBackend (default)
    |       |
    |       +-- docker run / docker exec / docker stop
    |       +-- Docker API fallback when no docker CLI
    |
    +-- RemoteSandboxBackend (reserved for remote provisioner mode)
```

### 3.3 当前最重要的设计分叉

当前系统里有两套“文件系统视图”，这不是重复实现，而是目标不同：

- **Agent 文件系统视图**
  - 关注工具调用和 prompt 约束
  - 通过 composite backend 暴露 `/mnt/skills`
  - 语义偏 agent-oriented

- **工作台文件浏览视图**
  - 关注真实目录浏览、原始文件内容、下载
  - 通过独立 viewer service 暴露
  - 语义偏 viewer-oriented

这套分离是当前架构里一个非常关键的改动。它解决了之前“工作台直接复用 agent backend 导致语义错位”的问题。

---

## 4. 运行拓扑

### 4.1 本地 Docker Compose 模式

在默认开发模式中，运行拓扑是：

```text
宿主机
├── Docker Daemon
│   ├── api-dev
│   ├── web-dev
│   └── yuxi-sandbox-<sandbox_id>
└── project workspace

api-dev 容器
└── Yuxi 后端进程
    └── 通过 docker CLI 或 Docker API 管理 yuxi-sandbox-* 容器
```

关键点：

- `api-dev` 并不在自身进程里执行用户命令
- 真正的执行发生在独立的 `yuxi-sandbox-*` 容器中
- `api-dev` 只是控制面，负责创建、发现、复用、销毁沙盒容器

### 4.2 本地容器模式与远程 provisioner 模式

[sandbox_provider.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/sandbox_provider.py) 会根据配置决定底层 backend：

- 如果设置了 `YUXI_SANDBOX_PROVISIONER_URL`
  - 使用 `RemoteSandboxBackend`
- 否则
  - 使用 `LocalContainerBackend`

当前主路径和测试覆盖都以 `LocalContainerBackend` 为主。

### 4.3 Docker CLI 与 Docker API 双路径

[local_container_backend.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/local_container_backend.py) 和 [sandbox_backend.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/sandbox_backend.py) 都支持两种运行方式：

- 有 `docker` CLI 时，优先走 `docker run`、`docker exec`、`docker stop`
- 没有 `docker` CLI 时，退回到 Docker Unix Socket API

这样做的原因很现实：

- 开发机和容器内环境不总是完全一致
- 有些部署只挂载了 docker socket，没有安装 docker CLI

这套 fallback 的目标不是优雅，而是保证控制面在不同运行环境下还能工作。

---

## 5. 生命周期设计

### 5.1 当前资源归属：thread-local

当前 provider 的核心规则是：

```text
thread_id -> deterministic sandbox_id -> sandbox container
```

对应实现见 [sandbox_provider.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/sandbox_provider.py)。

`sandbox_id` 由 thread_id 的 SHA256 前 8 位生成：

```python
hashlib.sha256(thread_id.encode()).hexdigest()[:8]
```

这个设计带来的性质是：

- 同一个 thread 多次访问时，倾向复用同一个沙盒
- 不同 thread 不共享容器
- thread 级的工作目录和运行上下文被绑定在一起

这与旧方案最大的差异在于：

- **旧方案倾向 user 级复用**
- **当前方案明确是 thread 级隔离**

### 5.2 acquire 的完整逻辑

`provider.acquire(thread_id)` 的流程不是单纯“起一个容器”，而是按顺序尝试：

1. 看当前 thread 是否已经绑定活跃沙盒
2. 看 warm pool 里是否有这个 sandbox_id
3. 看 Docker 里是否已经存在同名运行中容器
4. 如果都没有，才真正创建新容器

这个流程很重要，因为它同时兼顾了：

- 惰性初始化
- 同线程复用
- 进程重启后的容器发现
- warm pool 热复用

### 5.3 release 与 destroy 的语义

当前 provider 仍然保留了 `release` 和 `destroy` 语义，但它们已经不再作为前端公开 API 的一部分。

- `release(thread_id)`
  - 从活跃映射中移除
  - 放入 `_warm_pool`
  - 容器保持运行

- `destroy(thread_id)`
  - 同时处理 active 和 warm pool 中的对应沙盒
  - 真正调用底层 backend 停止容器

这里有一个历史经验教训：如果 `destroy()` 只认 active map，不认 warm pool，就会出现“释放后无法真正停掉容器”的泄漏问题。当前实现已经修正了这类问题。

### 5.4 空闲回收

provider 在初始化时会启动一个 idle checker 线程：

- 间隔：`IDLE_CHECK_INTERVAL`
- 默认空闲超时：`DEFAULT_IDLE_TIMEOUT = 600`

回收对象有两类：

- 活跃但长期无访问的沙盒
- 已经释放到 warm pool 且超时的沙盒

### 5.5 为什么现在不再依赖 `/api/sandbox/*`

当前公开路由注册见 [server/routers/__init__.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/server/routers/__init__.py)。可以看到：

- 已注册：
  - `/api/v1/filesystem/*`
  - `/api/v1/viewer/filesystem/*`
- 未注册：
  - `/api/sandbox/*`

这代表生命周期已经被收回到系统内部，不再由前端显式驱动。

这么做的核心理由有三个：

1. 避免前端为了“预热容器”而直接参与控制面
2. 避免 thread ownership 校验分散在多个入口
3. 让 Agent 工具调用和工作台访问都走同一种惰性初始化模型

---

## 6. 文件系统模型

### 6.1 真实宿主机目录

每个 thread 在宿主机侧都有独立的数据目录，根路径大致是：

```text
saves/threads/<thread_id>/user-data/
```

provider 会确保下面这些子目录存在：

- `workspace`
- `outputs`
- `uploads`
- `uploads/attachments`
- `large_tool_results`

对应代码在 [sandbox_provider.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/sandbox_provider.py) 的 `ensure_thread_dirs()`。

### 6.2 容器内固定命名空间

宿主机目录挂载进容器后，Agent 和工作台看到的是固定命名空间：

- `/mnt/user-data`
- `/mnt/user-data/workspace`
- `/mnt/user-data/uploads`
- `/mnt/user-data/outputs`
- `/mnt/user-data/large_tool_results`
- `/mnt/skills`

这里要区分两个概念：

- **标准命名空间**
  - 用于约束、归一化、提示词和工具行为
- **真实 listing**
  - 用于工作台展示实际文件结构

### 6.3 为什么工作台现在显示真实 listing

之前工作台复用 agent filesystem 视图时，一个典型问题是：

- Agent 在 `/mnt/user-data/bubble_sort.py` 创建了文件
- 工作台却只显示硬编码的 `workspace/uploads/outputs`
- 用户会误以为文件没有创建成功

所以当前 viewer service 的策略是：

- `/mnt/user-data` 按真实目录扫描结果返回
- 不再人为注入固定目录

这使得工作台真正展示“当前线程在后端实际看到的文件系统”。

### 6.4 兼容路径为什么还存在

[path_security.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/path_security.py) 中仍然保留了若干兼容映射，例如：

- `/workspace` -> `/mnt/user-data/workspace`
- `/uploads` -> `/mnt/user-data/uploads`
- `/outputs` -> `/mnt/user-data/outputs`
- `/skills` -> `/mnt/skills`
- `/attachments` -> `/mnt/user-data/uploads/attachments`
- `/home` -> `/mnt/user-data/workspace`

它们存在的原因不是鼓励继续使用旧路径，而是为了兼容：

- 模型自己生成的老路径
- 某些工具或 prompt 里的历史路径语义
- 迁移期间尚未完全收敛的调用方

文档层面应该明确：

- **标准输出和标准认知应基于 `/mnt/...` 命名空间**
- 兼容映射只是过渡层

---

## 7. Skills 挂载与可见性模型

### 7.1 Skills 不是简单的本地目录暴露

skills 在物理上会被只读挂载到容器的 `/mnt/skills`，但“能否看见全部 skills”不是由挂载本身决定，而是由上层 backend 路由决定。

当前相关逻辑分成两层：

1. **物理挂载**
   - provider 在 `_get_extra_mounts()` 中将 skills 根目录以只读形式挂进容器
2. **逻辑可见性**
   - [composite.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/composite.py) 和 [viewer_filesystem_service.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/services/viewer_filesystem_service.py) 基于当前 runtime context 的 `skills` 选择可见 skills

### 7.2 Agent 侧如何看到 Skills

Agent 侧创建 backend 时，会调用：

- [graph.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/buildin/chatbot/graph.py)
- [composite.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/composite.py)

结果是：

- 默认文件系统后端使用沙盒 backend
- `/mnt/skills/` 路由到 `SelectedSkillsReadonlyBackend`

所以 Agent 看到的 `/mnt/skills` 是“当前 agent config 允许它看到的 skills 子集”，而不是物理目录的全量镜像。

### 7.3 工作台为什么也要按 agent config 过滤 Skills

工作台的目标不是管理员视角的“看所有技能”，而是：

- “当前这个 thread 对应的 agent，在此刻实际能看到什么”

因此 viewer service 会先解析：

- 当前用户
- 当前 thread
- 当前 agent_id
- 可选的 agent_config_id

然后再决定 `/mnt/skills` 下暴露哪些内容。

这保证了两件事：

1. 工作台与 Agent 的 skills 可见范围一致
2. 前端不会看到“实际 Agent 用不到的 skills”

---

## 8. 路径安全与命令执行约束

### 8.1 核心约束原则

当前安全模型并不是一个完整的系统调用级隔离模型，而是建立在以下几层约束之上：

1. 线程级独立容器
2. 受限文件命名空间
3. 绝对路径白名单检查
4. 只读 skills 挂载
5. 前端/服务层的 thread ownership 校验

### 8.2 路径白名单

[path_security.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/path_security.py) 中定义了允许的命名空间前缀：

- `/mnt/user-data`
- `/mnt/skills`

只要请求落在这两个根之外，就会被拒绝。

同时还会显式拦截：

- `..` 路径遍历
- 非允许命名空间访问

### 8.3 命令执行时的路径检查

`validate_execute_command_paths()` 会对 shell 命令做 token 级别的绝对路径检查：

- `/mnt/...` 允许，但仍要经过 `ensure_path_allowed()`
- `/bin/`、`/usr/bin/`、`/usr/local/bin/` 放行
- `/usr/lib/` 作为运行时依赖路径放行
- 其他绝对路径拒绝

这意味着当前模型允许类似：

```bash
python3 /mnt/user-data/workspace/app.py
ls /mnt/user-data
/bin/sh -c '...'
```

但不允许：

```bash
cat /etc/passwd
python /tmp/evil.py
```

### 8.4 输出中的宿主机路径遮蔽

为了减少宿主机路径泄露，`sandbox_backend` 会对输出做一次映射遮蔽：

- 宿主机 thread 目录 -> `/mnt/user-data`
- skills 宿主机目录 -> `/mnt/skills`

这是一个很实用的细节。它不能提供真正的安全保证，但可以降低“内部物理路径暴露到模型上下文或前端界面”的概率。

### 8.5 需要明确的现实边界

当前这套安全模型是“工程上可控”，不是“高强度对抗级别安全”。

例如，当前仍然没有做到：

- 细粒度 CPU / memory / pid 限制
- 默认禁网
- 严格只读 rootfs
- seccomp / apparmor 的定制化收敛
- 进程树和系统调用级审计

当前本地容器启动只设置了：

- `--security-opt seccomp=unconfined`

这从强安全角度看其实是偏宽松的。文档必须如实反映这一点，而不是把当前方案描述成“高安全沙盒”。

---

## 9. Agent、旧 filesystem API 与 viewer API 的关系

### 9.1 Agent 侧接入

以 [chatbot/graph.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/buildin/chatbot/graph.py) 为例，FilesystemMiddleware 的 backend 由 `_create_fs_backend()` 动态创建。

关键流程是：

1. 从 runtime context 里拿到 `thread_id`
2. `provider.acquire(thread_id)`
3. 用 `create_agent_composite_backend()` 组装：
   - default = sandbox backend
   - `/mnt/skills/` = SelectedSkillsReadonlyBackend

这代表：

- Agent 的工具调用第一次触发文件系统时，就会惰性初始化沙盒
- 不需要先调用某个 prepare API

### 9.2 旧 `/api/v1/filesystem/*` 的定位

[filesystem_service.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/services/filesystem_service.py) 和 [filesystem_router.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/server/routers/filesystem_router.py) 仍然存在。

它们的定位是：

- 提供兼容性的 filesystem 访问接口
- 继续基于 agent-oriented composite backend 工作

它们会：

1. 校验 thread ownership
2. 解析当前用户可用的 agent config context
3. acquire sandbox
4. 组装 composite backend
5. 调用 `ls_info` 或 `download_files`

### 9.3 新 `/api/v1/viewer/filesystem/*` 的定位

viewer API 对应：

- [viewer_filesystem_service.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/services/viewer_filesystem_service.py)
- [viewer_filesystem_router.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/server/routers/viewer_filesystem_router.py)

这组接口的设计目标非常明确：

- 为工作台文件浏览器服务
- 强调真实目录浏览
- 返回原始文件内容
- 支持下载
- 与 Agent 的 skills 可见范围一致

当前提供三类只读能力：

- `tree`
- `file`
- `download`

为什么说它不是“API 参考”而是“技术设计”的一部分？因为它的重要性不在于 URL 形状，而在于 **它代表了工作台视图从 agent backend 中解耦** 这个架构决定。

### 9.4 为什么不让工作台继续复用旧 filesystem API

之前工作台直接复用 agent filesystem 逻辑，会遇到一串语义问题：

- 文件读取带行号，因为底层 `read()` 更偏向 Agent 阅读语义
- 目录展示不够真实，更偏向工具侧规范目录
- skills 可见性容易与当前 agent config 脱节
- 前端不得不理解更多 backend 细节

viewer API 的存在，实质上是承认：

- “Agent 文件系统”与“用户浏览器文件系统”是两个不同产品对象

这是当前沙盒周边设计里最重要的一个收敛。

---

## 10. 典型执行流程

### 10.1 Agent 首次使用文件系统

```text
用户发起对话
  -> Agent graph 构建 middleware
  -> Agent 首次触发 filesystem tool
  -> _create_fs_backend()
  -> provider.acquire(thread_id)
  -> 若无现成容器则创建新容器
  -> 返回 composite backend
  -> Agent 开始读写 /mnt/user-data 或访问 /mnt/skills
```

### 10.2 工作台首次打开文件系统

```text
前端打开 AgentPanel
  -> 请求 /api/v1/viewer/filesystem/tree?thread_id=...&path=/
  -> 后端校验 thread ownership
  -> 解析当前 agent config
  -> provider.acquire(thread_id)
  -> 构造 sandbox backend + skills backend
  -> 返回根目录条目
```

### 10.3 点击进入某个目录

```text
前端点击目录
  -> 请求 viewer tree(path=<that dir>)
  -> 后端仅列当前层目录
  -> 前端懒加载子节点
```

这意味着工作台不会一次性扫完整棵树，而是按层级逐步展开。

### 10.4 读取文件内容

```text
前端点击文件
  -> 请求 /api/v1/viewer/filesystem/file
  -> 后端 download_files([path])
  -> 取原始 bytes
  -> UTF-8 decode，失败时 replace
  -> 返回纯文本内容
```

这里特意不走 deepagents 的 `read()`，就是为了避免“所有文件都被自动加行号”的 agent-oriented 行为污染工作台体验。

---

## 11. 容器管理细节

### 11.1 命名规则

容器名格式：

```text
yuxi-sandbox-<sandbox_id>
```

其中 `<sandbox_id>` 是 thread_id 的确定性哈希截断值。

### 11.2 启动方式

本地容器 backend 典型启动命令等价于：

```bash
docker run \
  --security-opt seccomp=unconfined \
  --rm \
  -d \
  -p <port>:8080 \
  --name yuxi-sandbox-<sandbox_id> \
  -v <thread_user_data_dir>:/mnt/user-data \
  -v <skills_root>:/mnt/skills:ro \
  <sandbox_image> \
  sh -c "sleep infinity"
```

为什么是 `sleep infinity`：

- 容器被创建后保持存活
- 真正的执行通过后续 `docker exec` 进入
- 这样可以避免每次命令都重新起一个新容器

### 11.3 容器发现

当 provider 启动后，如果内存态映射丢失，但 Docker 中容器还活着，provider 会尝试 `discover(sandbox_id)`：

- 根据容器名判断是否存在
- 解析暴露端口
- 重建 `SandboxInfo`

这是一种“弱恢复”能力，不是严格的持久化控制面。

### 11.4 端口分配

本地 backend 会从 `base_port` 开始找空闲端口，并向后扫描。

需要诚实指出：

- 这是典型的“先探测端口再绑定”的 TOCTOU 模式
- 在高并发/高竞争环境下并不完美

当前之所以可接受，是因为它主要服务于本地开发和较小规模部署，而不是大规模沙盒平台。

---

## 12. 配置项

当前配置核心定义见 [sandbox_config.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/package/yuxi/agents/backends/sandbox_config.py)。

常用项包括：

- `YUXI_SANDBOX_IMAGE`
  - 沙盒镜像
- `YUXI_SANDBOX_BASE_PORT`
  - 本地容器端口起始值
- `YUXI_SANDBOX_CONTAINER_PREFIX`
  - 容器名前缀
- `YUXI_SANDBOX_IDLE_TIMEOUT`
  - 空闲超时
- `YUXI_SANDBOX_MAX_REPLICAS`
  - 最大并发沙盒数
- `YUXI_SANDBOX_HOST`
  - 宿主机访问沙盒容器的 host
- `YUXI_SANDBOX_PROVISIONER_URL`
  - 远程 provisioner 模式入口
- `YUXI_DOCKER_API_BASE`
  - Docker API base
- `YUXI_DOCKER_API_SOCKET`
  - Docker Unix Socket 路径

理解这些配置时，要把它们分成三类：

- **容器资源寻址**
- **provider 生命周期控制**
- **Docker 控制面访问方式**

---

## 13. 测试与验证策略

### 13.1 单元与集成测试

当前沙盒和 viewer 相关测试覆盖包括：

- [test_sandbox_provider_lifecycle.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/test/test_sandbox_provider_lifecycle.py)
- [test_sandbox_path_compat.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/test/test_sandbox_path_compat.py)
- [test_filesystem_router.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/test/api/test_filesystem_router.py)
- [test_viewer_filesystem_router.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/test/api/test_viewer_filesystem_router.py)

覆盖点主要包括：

- provider 生命周期逻辑
- 路径兼容与路径安全
- 旧 filesystem API 的 thread ownership 和命名空间行为
- viewer API 的真实目录浏览、原始文件读取、下载行为

### 13.2 端到端测试

脚本式 E2E 见：

- [test_sandbox_e2e_no_skip.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/test/api/test_sandbox_e2e_no_skip.py)
- [test_sandbox_e2e_reuse.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/test/api/test_sandbox_e2e_reuse.py)
- [test_viewer_filesystem_e2e.py](/Users/wenjie/Documents/projects/Yuxi-Know/backend/test/api/test_viewer_filesystem_e2e.py)

这些脚本验证的是更接近真实使用链路的场景：

- thread 创建
- provider acquire
- 容器内写文件
- API 层读取/浏览/下载
- 同线程复用
- 不同线程隔离

### 13.3 为什么测试里要主动清理 sandbox 容器

由于本地容器模式本质上会真正起 `yuxi-sandbox-*` 容器，如果测试没有处理好清理，会出现：

- 端口耗尽
- warm pool / discover 干扰后续用例
- 测试之间状态污染

因此测试基建里增加了 session 级 sandbox cleanup。这个细节本身也说明：

- 当前 provider 的控制面仍偏内存态
- 真实容器状态与测试进程状态可能漂移

---

## 14. 当前局限性

### 14.1 安全强度仍然偏工程化

当前方案不是强隔离安全产品，其局限包括：

- 没有严格资源配额
- 没有默认禁网策略
- 容器启动参数偏宽松
- 依赖路径白名单而不是系统调用级控制

### 14.2 Provider 状态未持久化

provider 的核心状态都在进程内：

- `_sandboxes`
- `_sandbox_infos`
- `_thread_sandboxes`
- `_warm_pool`

这意味着：

- 进程重启后要依赖 `discover()`
- 不能把当前方案理解为“有完整控制数据库的沙盒平台”

### 14.3 viewer 仍然是只读

当前 viewer-oriented filesystem service 只做了：

- tree
- file
- download

没有做：

- rename
- move
- delete
- upload
- inline edit

这是刻意收敛范围的结果，不是遗漏。

### 14.4 兼容路径仍然存在

兼容路径仍保留，说明路径模型虽然已经大致收敛，但还没有完全把所有调用方都清到统一规范。

### 14.5 本地容器 backend 仍有工程折中

例如：

- 端口分配不是完全无竞争
- Docker CLI / Docker API 双实现会增加维护成本
- 文件上传下载与命令执行仍不是完全同一种底层路径

这些都是当前方案的真实成本。

---

## 15. 后续演进方向

如果未来继续演进，这条线最值得做的事情不是再堆更多 viewer 功能，而是继续收敛基础模型：

### 15.1 强化控制面

- 将 sandbox 元数据持久化
- 更清晰地区分 active、warm、destroyed 状态
- 降低 discover 对系统一致性的依赖

### 15.2 强化安全边界

- 增加资源限制
- 明确网络策略
- 收紧容器安全选项
- 评估更适合沙盒场景的运行时

### 15.3 继续收敛路径模型

- 逐步减少兼容别名
- 统一让 `/mnt/...` 成为唯一规范路径
- 减少模型与前端对历史路径的依赖

### 15.4 继续拆分用户视图与 Agent 视图

viewer service 证明了一件事：

- “给 Agent 用的 backend”和“给用户看的文件浏览器后端”应该明确区分

如果以后要做：

- 文件编辑器
- 差异查看
- 搜索
- 批量下载
- 二进制预览

应该继续沿 viewer-oriented 的方向扩展，而不是回退去复用 agent-oriented backend 语义。

---

## 16. 一句话总结

Yuxi 当前的沙盒方案，本质上是一套 **线程级容器工作区 + `/mnt` 命名空间 + 惰性获取的 provider + Agent/Viewer 双视图文件系统接入模型**。

它已经足够支撑：

- Agent 在独立线程工作区中执行命令
- Skills 以只读方式注入
- 工作台查看真实后端文件系统

但它仍然是一个 **面向当前产品场景的工程化沙盒**，而不是一个已经完成强安全与强控制面建设的通用沙箱平台。
