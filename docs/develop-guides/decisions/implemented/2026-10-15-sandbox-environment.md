# Cube 用户沙盒环境、模板与文件边界

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/runtime/sandbox/provider.py

## 问题

用户环境需要保留软件与配置、支持共享 Workspace、并发命令、变量生效验证和可观察休眠状态；共享挂载故障还要求保留现有 VM 与文件恢复。

### Cube 用户环境与共享文件接入

用户需要跨对话保留普通账号的软件和配置，休眠时仍可编辑 Workspace，并允许同用户 Agent 自然并发。运行作用域拥有且空闲删除的容器环境不能满足这一生命周期。

### 用户环境变量生效验证

用户需要确认已保存的变量进入真实新命令，而非仅确认配置保存成功。变量更新不改变已经启动的进程。固定 Cube 版本缺少保留 Home 的公开重启接口，用户接受禁用按钮并说明限制。

### 嵌套 Cube 共享挂载恢复与压力验证

开发执行节点的外层 virtiofs 请求阻塞，Cubelet 挂载进程停在 FUSE 等待，用户环境恢复和新命令失败。virtiofsd 与 Cube 服务仍存活，进程健康无法证明共享文件通道可用。恢复必须保留已有用户 VM、Home 软件与配置和宿主共享文件。事实时间线及因果边界见[事故复盘](../../postmortems/2026-10-15-cube-shared-mount-stall.md)。

### 运行环境设置的信息层级

设置页的状态、说明与变量编辑缺少主次，重复的成功文案与验证时间挤占空间。用户需要紧凑状态栏、右对齐菜单、一行一个横向变量卡片，以及直观的磁盘用量条。

### 用户环境磁盘用量简要查询

环境设置需要简要展示已用和可用空间。Cube 当前查询接口没有这些实时数值；环境状态轮询不能为了采样持续执行命令、重置空闲时间或唤醒休眠实例。

### 用户环境休眠时间与空闲倒计时

Agent 环境设置只能显示状态和固定五分钟说明，无法判断何时休眠、已休眠多久以及距离空闲休眠阈值还有多久。现有 last_active_at 不拥有休眠时间，Cube 0.7.3 查询接口没有暴露 pause_at，不能从活动时间推测历史休眠事实。

### 默认模板与远程 Skill 安装

普通用户 VM 需要直接执行现代 Node CLI、Python Skill、网页与文档处理。远程 CLI 在临时 HOME 生成的缓存链接必须能安全清理，否则下载成功也无法进入个人技能发布。默认模板既要提供真实可用的依赖，也要保留已有用户 VM 与持久路径。

常规系统 CI 的沙盒选择及 Cube 探针触发策略由[container 运行链路决定](2026-10-23-container-runtime-ci.md)部分取代；本记录的真实 Cube 验证结果仅对应其指定提交与环境。

## 决策

当前沙盒决策覆盖持久用户环境、后端装配、文件边界、模板依赖、运行与休眠状态、共享挂载恢复、命令验证和空间展示。每个事实仍由对应源码、数据库、Compose、机制文档和测试拥有；本记录归并其取舍与证据入口。

旧 Docker provisioner 曾在 daemon 创建边界配置容器内存、CPU 和进程数上限；该后端随 Cube 执行路径切换删除。历史限制不构成 Cube VM 的配额承诺，也不应据此推断当前环境具有相同上限。

### Cube 用户环境与共享文件接入

#### 实现方案

统一工具 Backend 将 Workspace 和获授权 Skills 路径交给宿主 no-follow 文件边界，将其他文件和命令交给固定普通账号的 Cube VM 进程。Cube HTTP 文件接口实际可读 root 文件，即使传入 username=user 仍不满足权限要求，因此删除该通道；所有 VM 文件操作进入持久命令句柄与进程组清理。CubeAdapter 由正式 runtime/sandbox/cube.py 拥有；实验 sandbox-lab 已删除。旧环境数据直接清除，不迁移软件与 Home。个人 Skills 属于 Workspace，共享 Skills 只挂用户授权投影且只读。远程 Skill CLI 在操作者的同一环境运行，只清理请求临时目录。

用户环境和活动命令句柄由 PostgreSQL repository 拥有。用户生命周期锁协调首次创建、恢复、空闲暂停、重建与执行领取，命令不串行。Run/attempt 仍拥有执行租约、结果、取消与失联收尾。待清理命令阻止暂停和重建；旧代次取消不能影响新实例。进程关闭不销毁环境，失联不自动重建。

需要继续使用指定 VM 的操作在用户生命周期锁内核对代次和实例 ID，若来自 Run 还须核对执行租约，再创建或唤醒环境。调用方只获得代次和实例 ID。平台在指定的运行实例里执行受信程序时，在用户锁内核对代次和执行身份，将参数与私有标准输入分开发送，读取退出码及有界输出。状态读取不唤醒实例，也不延长空闲计时。来宾应用的安装和协议由镜像及对应接入模块维护，Cube 直接按实例执行休眠。

进程启用标准输入时，Cube 适配器分块发送字节，再调用 `CloseStdin` 发送 EOF，空输入直接关闭。输入管道已关闭或对应进程已退出时，适配器继续读取原 `Start` 流确认退出码和输出。协议由[默认模板的 envd](https://github.com/e2b-dev/infra/blob/2026.16/packages/envd/internal/services/process/input.go)提供。

故障恢复沿用设置里的“重建环境”。只有持久故障或已中断的生命周期操作支持按故障代次重建。页面将用户确认的代次交给后端，后端在同一把锁内复核，代次已变或故障已解除时返回冲突。节点暂时查不到实例时只提示查询失败。

Web 用户设置只增加状态和重建操作。正在使用环境的 Run 和其他 VM 操作阻止重建；重建清除 Home 软件与配置，保留 Workspace 与 Skills。单节点挂载同一份宿主文件，不增加同步、备份、历史版本或多环境选择。用户 Workspace 直接挂载普通用户 Home 下的固定目录，路径与删除兼容的决定见[工作区路径清理](./2026-10-15-workspace-path-cleanup.md)。

### 用户环境变量生效验证

#### 实现方案

环境配置 service 拥有 POST /api/user/agent-env/verify 用例，认证上下文决定用户身份。验证经既有持久执行器进入用户 VM 的普通 shell 命令，预期值只经 stdin 传入，响应仅含变量名、匹配结果、配置更新时间和验证时间。执行前后回读 PostgreSQL，期间配置改变返回 409。验证由用户点击触发，允许创建或唤醒环境，不改变现有 Run 的执行、取消与归属。

普通命令使用[Run 执行配置快照](./2026-10-15-run-environment-snapshot.md)，手动验证使用最新保存值。设置页通过按需说明展示新运行生效与当前执行保持快照的边界。未保存编辑禁止验证；编辑、刷新和保存开始就使未完成的验证失效，结果与当前已加载配置时间一致才展示。验证后刷新环境状态。不提供保留 Home 的重启入口。

取消清理直接通过既有普通用户 Python 进程通道执行，不读取用户 shell profile。用户 profile 可阻塞自己的 shell 命令，但不能阻断所属进程组清理或文件工具；代次与进程组归属验证保持。

### 嵌套 Cube 共享挂载恢复与压力验证

#### 实现方案

外层 QEMU 的 virtiofs 设备显式使用 `queue-size=1024`，与[上游 standalone 示例](https://virtio-fs.gitlab.io/howto-qemu.html)一致；共享目录、文件身份能力、`cache=never` 与子挂载通告策略保持原配置。这个值是启动配置选择，尚无证据证明默认 128 就是历史阻塞的必要原因。

现场恢复先停止新任务并确认空闲，保存私有环境引用、共享文件归档与哈希，在节点停止后备份并检查 qcow2。恢复同一节点磁盘后，provider 重连原实例；不调用 rebuild/destroy 操作现有用户。两个原环境的实例标识、代次及 2547 个共享文件/链接校验一致，原 Home 在三轮命令、休眠和恢复间保持一致。

`test/integration/sandbox/conftest.py` 复用原真实用户资源 fixture，每个场景仅回收自己创建的用户、VM 和文件。固定 seed fuzz 覆盖 1/2/4/8 并发、4 KiB 至 1 MiB 文件、特殊合法文件名、fsync 与原子替换、宿主字节回读；恢复后先核对上一阶段字节再覆写。命令 fuzz 核对普通用户命令的准确输出、正常及非零退出和持久句柄清理。丢失旧文件的负向用例验证恢复 oracle 会在覆写前失败。现有系统 workflow 执行整个 sandbox integration 子树。

### 运行环境设置的信息层级

#### 实现方案

`frontend/src/modules/settings/ui/AgentEnvSettingsCard.vue` 拥有保存与验证流程；`UserSandboxSettingsCard.vue` 以紧凑状态栏展示状态和休眠时间，右侧显示磁盘数字与细用量条，不使用图标底座、竖分隔线和常驻共享说明。

只有设置页消费的变量编辑器归属 settings 的 `AgentEnvEditor.vue`，复用原有草稿同步、已保存键锁定和值显隐逻辑。每个变量独占一张横向卡片，变量名用代码文本展示，值使用输入框；添加与刷新靠近列表，保存与验证位于底部。成功只在变量名后显示绿色勾号，异常显示“不一致”，不额外展示成功汇总或验证时间。生效范围通过可聚焦的帮助入口查看，该入口悬停保持透明背景。

`frontend/src/shared/ui/ActionDropdown.vue` 拥有菜单定位，`align="end"` 根据触发器右边缘定位并在视口边缘钳制，默认起端对齐行为保持。环境操作只消费该定位能力，继续复用键盘关闭、焦点归还和重建确认。账户配置与沙盒 API、持久状态和生效范围不变。

### 用户环境磁盘用量简要查询

#### 实现方案

复用现有普通用户执行通道读取 /home/user 所在文件系统的 statvfs。Web 认证 POST 只接收当前用户上下文，等待已有 executor 完成执行和句柄清理，返回已用、可用字节；非零退出、截断或不合法数值显式失败。沿用执行通道的恢复与空闲计时行为，不增加后台采集、缓存或持久字段。

未查询时只显示“查看用量”，成功后隐藏入口，不额外显示刷新图标。容量条按已用占“已用 + 当前用户可用”空间的比例绘制；数字复用文件大小格式，不宣称包含文件系统预留空间。未查询、读取失败或空间合计为零时不画比例条。按钮标题与无障碍名称披露休眠唤醒行为，统计范围注明不含共享工作区；重建代次变化、状态断线和卸载作废旧结果及在途请求。状态轮询不触发磁盘查询，磁盘读取失败清除旧数值并重新读取环境状态。用户操作及统计范围由[沙盒运维参考](../../../advanced/sandbox-operations.md#运维与验证)拥有。

### 用户环境休眠时间与空闲倒计时

#### 实现方案

user_sandboxes 的 nullable paused_at 拥有平台确认休眠完成的时间，app-init 从当前 ORM 直接创建该列，数据库初始化遵循[首个 Schema 基线决策](2026-10-19-schema-baseline-one.md)。CubeAdapter.pause 返回是否由本次操作确认休眠，provider 仅在确实执行休眠后记录确认时间，观察到既有外部休眠时保留未知，创建或成功恢复后清除旧时间。Web 状态查询返回 UTC 时间、实际空闲阈值和服务端观察时间；仅运行空闲且无错误时给出预计休眠阈值时间。休眠与命令策略沿用原 Owner。

设置卡片复用现有上海时区日期格式与状态标签，在状态区域展示休眠确认时间和时长，或已空闲时长与预计剩余时间。浏览器时间按服务端快照校正，每秒本地更新时长、每 15 秒重新读取真实状态，卸载清理定时器和迟到查询。阈值到达只显示等待休眠，忙碌状态不提供倒计时；历史时间为空则显示记录未知。接受重建结果时清除旧刷新资格及 loading，旧请求无法覆盖新环境。当前用户文案和运维行为由[沙盒运维参考](../../../advanced/sandbox-operations.md#运维与验证)拥有。

### 后端装配、文件搜索与 CI 准备

#### 实现方案

`sandbox/backend.py` 直接从已准备 Context 装配 `CompositeBackend`，显式要求用户身份与 Workdir，产物根保持当前 Project 的 `outputs`。文件与摘要 middleware 共用构图时创建的 backend，构造不创建或唤醒 VM。runtime scope 只表达协作树上下文，持久 VM 的身份仅由 uid 决定；不从 checkpoint thread 猜测环境身份，也不恢复独立 composite 模块和一次性 scope 类。

文件搜索沿当前文件路由执行：Workspace 与授权 Skills 由 `RuntimeFiles` 的宿主 no-follow 边界读取，其他目录由普通用户 VM 的可取消命令返回结构化路径、行号与文本。旧 AIO `/v1/file/grep`、agent-sandbox SDK 与 provisioner 凭据随旧后端删除，其隐藏文件、链接、限额和异步超时语义不构成 Cube 契约。

系统测试使用具有 KVM、Cube 控制面、数据代理和已配置模板的专用 runner，配置准备由 `scripts/ci_prepare_system_tests_env.sh` 拥有，真实 sandbox integration 由 `system-tests.yml` 执行。旧 Docker 镜像预拉取步骤随 provisioner 删除；环境准备与 Run 执行预算仍分开，不能通过延长 Run 超时或 mock execute 掩盖环境未就绪。远程 runner 的实际执行状态单独记录。

### 默认模板工具链与浏览器、文档能力

#### 实现方案

`docker/cube/Dockerfile.user` 在官方 Cube 基础镜像上提供 Node 24、Python 3.13 和 uv/uvx。Node 来自官方 24 系列镜像，uv 复用 API 的 0.12.6；uv 在构建时安装固定 Python 3.13.15，并将默认命令发布到 `/usr/local/bin`。Ubuntu 的 `/usr/bin/python3` 继续由系统管理，用户可通过 uv 创建项目环境和安装其他解释器；构建专用安装目录只在该命令内设置。

模板预装基础 CLI `curl wget jq ripgrep file zip unzip`、Git、CA 证书、中文字体 `fonts-noto-cjk`、PDF 工具 `poppler-utils`，以及固定版本的 Python Playwright、httpx、BeautifulSoup、Pillow、openpyxl、PyMySQL。Playwright 1.64.0 的安装器提供配套 Chromium 与系统依赖，浏览器缓存位于普通账号 Home，并在构建末尾交给该账号。具体包版本由 Dockerfile 拥有。

`/home/user/workspace` 是唯一持久工作区挂载，`/home/user/skills` 是只读授权投影，实际 Home 为 `/home/user`。路径与清理边界由[工作区路径清理](./2026-10-15-workspace-path-cleanup.md)及 `runtime_paths.py` 拥有；旧挂载和旧引用没有兼容读取。

独立模板通过普通用户验收后，按维护请求将默认 `yuxi-user` 别名指向新模板。新建或显式重建的 VM 使用新模板，已有 VM 保留原实例。范围不包含桌面、VNC、VS Code、常驻浏览器服务、新浏览器 API、编译工具、多媒体和全部数据科学库。

#### 官方镜像比较

火山公开镜像 `all-in-one-sandbox:1.11.0` 对应 [agent-infra/sandbox 官方发布](https://github.com/agent-infra/sandbox/blob/7f1afaf8d82bd30531a19caeb1a24dfebbc97d8c/README.md)。该提交没有公开 Dockerfile，版本事实来自只读检查 linux/amd64 的 [OCI config/history](https://enterprise-public-cn-beijing.cr.volces.com/v2/vefaas-public/all-in-one-sandbox/blobs/sha256:c8c90673082b6d4bf6517bda004cbf8b6059fd17a0866d1971e17cce3da18ee9)，没有拉取或运行其文件系统 layer。

| 工具 | AIO 1.11.0 构建记录 | Yuxi 选择 |
|---|---|---|
| Node | 安装 20/22/24，默认和通用命令选择 22 | 默认 24，按用户要求升级 |
| Python | Ubuntu 系统 Python，另安装 3.11.13、3.12.11；服务明确使用 3.12，普通 Shell 默认未实测 | 默认 3.13.15，与 API 的 minor 一致 |
| uv/uvx | 镜像包含两者，精确版本未核实 | 0.12.6，与 API 复用已有版本 |
| 工作目录 | 官方 `WORKSPACE` 默认 `/home/gem`，支持显式指定目录 | 普通用户 Home 下的固定 Workspace 和只读投影路径 |

AIO 的[环境变量](https://sandbox.agent-infra.com/zh/guide/advanced/env-config)与[工作目录](https://sandbox.agent-infra.com/zh/guide/advanced/workspace)说明没有要求 `/home/user/workspace`。该完整路径属于 Yuxi 自有文件契约。官方[沙盒信息页](https://sandbox.agent-infra.com/zh/guide/basic/sandbox)中的 Python 3.12.11、Node 22.19.0、uv 0.8.9 是旧版 `v1.0.0.93` 示例，不作为 1.11.0 或 Yuxi 当前运行版本的证据。


### 远程 Skill 临时目录清理

#### 实现方案

`Workspace` 删除边界使用父目录文件描述符和 `unlink` 删除符号链接目录项，保持父目录遍历与子目录打开的 no-follow 约束。CLI 临时 HOME 可以包含指向请求外部或用户文件的任意链接；删除只移除链接本身，读取、写入和复制仍受原有链接限制。

`remote.py` 拥有请求临时目录与清理时序，`personal.py` 拥有安装发布。模板提供 Node/npm、Git 与 CA 证书，避免每次安装再升级工具链。清理失败显式返回错误，不忽略异常，不扫描或删除历史失败目录，不因安装失败自动重建已有 VM。

## 替代方案

以下各主题保留真实替代方案与接受的代价；Docker provisioner 专属资源限制已随旧执行后端删除，不再作为当前 Cube 环境的配置承诺。

旧 Docker 后端将资源限制放在 Docker daemon 创建容器时执行。只依赖命令超时不能限制资源，API/worker 的外层检查可被绕过；Kubernetes backend 当时没有可验证集群，未扩展未验证的资源配置。

### Cube 用户环境与共享文件接入

继续使用旧 Docker provider 不能保留用户环境。并存两种执行 provider 会增加配置和生命周期分歧，因此替代验收后删除旧后端。把 Home 全部放入 Workspace 会混淆文件成果与软件环境，因此 Home 文件通过 Cube 访问。远程 Skill CLI 与用户 Agent 共享普通用户权限，接受与用户电脑执行相同的访问范围，不继续维护一次性执行环境。

### 用户环境变量生效验证

保存后自动验证会无意唤醒环境，因此提供显式按钮。只检查数据库不能证明 shell 配置覆盖后的真实环境。升级 Cube 或直接操作节点 hypervisor 引入新的运维边界，当前不实现。

### 嵌套 Cube 共享挂载恢复与压力验证

单独重启可以清除当前阻塞，但不提供持续回归证据。替换共享协议、升级 Cube 或调整缓存策略会扩大数据兼容与部署范围，当前没有足够的因果证据支持。重建用户 VM 会清除 Home 软件和配置，与保留现有环境的要求冲突。

### 运行环境设置的信息层级

只压缩表格文案不能解决控件语义与层级；只有设置页消费编辑器，没有并行维护表格与卡片两种模式的需求。页面自行覆盖浮层位置会绕过共享组件的视口和键盘行为，因此对齐能力仍由共享菜单拥有。没有新增依赖、持久状态、后端接口或备用展示路径。

### 用户环境磁盘用量简要查询

自动采样需要独立监测协议或后台缓存，超过当前简要展示需求。查询整个宿主机磁盘不能表达用户环境视角。使用现有命令通道手动读取，保持改动范围最小。

### 用户环境休眠时间与空闲倒计时

使用 last_active_at 当休眠时间会产生错误事实。读取 CubeMaster 私有 API 或日志会引入新的管理连接和字段依赖。只显示静态五分钟说明不能满足需求。选择一个持久时间字段与既有状态查询扩展。

### 后端装配、搜索与模板依赖

独立 composite 文件、一次性 scope 类和多来源字段解析没有第二个当前消费者；直接使用沙盒 backend 又会丢失 DeepAgents 所需的 `artifacts_root`。选择现有 `CompositeBackend` 的集中装配，并保留惰性创建。旧 AIO 原生 grep 曾用于避免 shell NUL 文本传输丢失和重复搜索实现；Cube 切换后，文件权限与执行生命周期由当前宿主和 VM 路由拥有，不保留第二套原生 API。

继续发行版 Python 3.10 不能满足当前 Python 语法基线与现代 Skill；全量采用 AIO 会引入桌面与额外服务，并需重新验证 Cube 启动和权限装配。仅安装 Python Playwright 缺少配套浏览器和系统库，使用 root 默认缓存则普通账号无法发现浏览器。模板预装最小当前依赖，项目依赖由 uv 管理。

迁移到 `/home/user/workspace` 需要同时迁移附件、Skill、工具引用和挂载，超出工具链更新范围。在业务安装链路中临时补包增加网络与环境副作用，普通用户也不能通过 apt 补系统包。用沙盒 `rm -rf` 清理绕过宿主边界；忽略清理错误会留下缓存或掩盖其他失败；禁止 CLI 创建链接不能覆盖 npm 缓存和仓库自身的链接。

## 后果

旧 Docker provider 的历史默认值为内存 2g、CPU 2、进程数 512；重依赖安装可能触及内存上限，进程数触顶时 fork/clone 会以资源错误退出，已创建容器也不会回溯应用新上限。这些后果只描述已删除的 Docker backend，不描述当前 Cube VM。

### Cube 用户环境与共享文件接入

当前环境为嵌套 Linux/KVM 实验节点，原生部署、资源压力和节点异常恢复尚未验证。普通进程组取消不保证终止主动 setsid 脱离的守护进程。远程 Skill CLI 可访问操作者环境，平台管理凭据及其他用户文件仍不得进入 VM。软件持久化不承诺后台进程和内存连续。已知 Cube 版本不支持保留 rootfs 的冷重启。

### 用户环境变量生效验证

验证仅证明该次新 shell 命令的实际变量，不保证正在运行的程序已经更新或第三方凭证可用。启动配置覆盖变量时报告不匹配，破坏命令执行时返回固定错误，不返回敏感输出。没有新增持久状态、轮询、环境选择或执行队列。

### 嵌套 Cube 共享挂载恢复与压力验证

阻塞节点无法正常关停时，仍存在强制退出风险。现场先停止 MySQL、同步根文件系统，再等待节点关闭；等待超时后的容器退出码为 137。离线原盘与备份均通过 `qemu-img check`，恢复后的用户身份与文件另行核验。备份包含敏感信息，只保存在私有、被 Git 忽略的运行目录。

1024 配置与节点恢复共同应用，缺少历史触发条件的稳定 reproducer，无法拆分两者对恢复的贡献。旧 128 配置的隔离磁盘副本通过短时文件、挂载生命周期和元数据压力对照，未复现阻塞。保留故障前磁盘证据，不把短时 fuzz 通过等同于长期消除内核/virtiofs 通知问题。后端源码生效时点由[开发环境优化决策](2026-10-15-other-optimizations.md)拥有。

### 用户环境磁盘用量简要查询

手动查询会恢复休眠实例并刷新最后活动时间，按钮标题与无障碍名称提示该行为。数值表示环境内可见文件系统的空间，包含系统占用，不代表宿主 qcow2 物理分摊或独占配额；共享工作区不在此统计范围。无需新增 Schema、依赖、部署服务或完整监控面板。

### 用户环境休眠时间与空闲倒计时

后端源码生效需要显式重启。未由平台确认的休眠时间保留 null；paused_at 表示平台确认完成时间，不承诺与 VM 内核冻结瞬间完全相同。休眠阈值与实际维护循环执行时点不同，界面以预计和等待休眠表达。后台恢复、命令与任务语义保持原行为。当前[共享初始化](2026-10-18-app-initialization.md)由 app-init 拥有初始建表，API/worker 只读校验版本。Public API 没有新增字段或入口。

### 模板升级与安装边界

Node 24 系列标签随上游维护更新，构建验收记录实际 patch；Python、uv 与 Python 包版本由模板固定。浏览器、字体和系统依赖增加体积，只预装 Chromium，客户端与浏览器需要配套升级。普通账号的项目依赖通过 uv 管理，系统 Python 与默认开发 Python 并存。

模板切换只影响新实例；显式重建会清空 Home 软件、配置和浏览器缓存，不能自动对已有用户执行。符号链接删除不授予目标访问权限，并发替换仍由文件描述符、no-follow 与可选 inode 身份约束处理。真实安装依赖 Cube、npm 与 Git 网络可用性。

## 验证

以下各节保留对应决定实施时的验证范围与结果；运行环境设置的浏览器证据不替代真实数据库、沙盒与 worker 的链路证据。

旧 Docker 限制实现曾以 `1g/1.5/256` 创建容器，并从 `docker inspect` 及容器 cgroup 回读 `Memory=1073741824`、`NanoCpus=1500000000`、`PidsLimit=256` 及对应 `memory.max`、`pids.max`、`cpu.max`。unit 覆盖默认值、环境覆盖、8 类非法配置、启动失败及 Compose 透传。该证据只验证当时 Docker provider，不是 Cube 资源上限证据；Kubernetes backend 的 Pod 限制未运行验证。

### 运行环境设置的信息层级

- `docker compose exec -T frontend pnpm run lint:check`：Passed。
- `docker compose exec -T frontend pnpm run test:unit`：Passed，1084 项全部通过；包含默认及末端定位、左右边缘和视口偏移用例，以及配置验证失效、磁盘零用量、25% 比例、可用空间耗尽、零分母不画条、查询后入口消失和失败清理回归。
- `docker compose exec -T frontend pnpm run build`：Passed。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`：Passed，3766 passed、127 skipped、7 subtests passed。
- `playwright-cli -s=runtime-refinement run-code --filename=frontend/test/browser/runtimeSettings.js`：Passed。在真实设置弹窗中验证浅深色、1440/1024/768px、长名称、菜单右对齐及 Escape 焦点、显隐、添加/保存后回读/删除、成功单勾号与不一致、草稿失效、无 hover 底色、容量条实际宽度、loading、empty、error 和任务级临时环境。
- `playwright-cli -s=runtime-strip-status run-code --filename=frontend/test/browser/sandboxTiming.js`：Passed，状态时长、忙碌禁用、重建取消、手动磁盘查询、失败清理和卸载停止轮询回归通过。

浏览器响应由合成 API 提供，截图与 DOM 证明装配和交互，不证明真实数据库或沙盒执行结果。未修改后端行为，未运行真实沙盒与持久化 E2E。

### Cube 用户环境与共享文件接入

- 文件 Backend unit 从宿主文件回读分页、编辑、搜索、共享 Skills 只读及 symlink 越界拒绝；协议 unit 检查不完整帧、断流、大小预算及代理空断连。
- 固定模板上的真实进程以读取至 EOF 的摘要和退出码验证空输入、无换行数据及多块字节；协议 unit 覆盖提前关闭管道后读取终态、断流与非预期请求错误。
- 真实 PostgreSQL/HTTP/Cube integration 验证并发首次使用一个 VM、Home 文件唤醒、sudo 拒绝、venv 软件持久化、休眠修改共享文件、空闲重建、忙碌 409、超时与执行者崩溃后的持久句柄清理、旧代次拒绝。
- 真实 Agent E2E 经 HTTP、worker 和 LangGraph 验证两个 Run 并行、取消 A 父子进程后文件停止增长、B 文件完成、第三个 Run 可用；取消监听直接调用所属命令清理，不能仅依赖图的关闭收尾。
- 两个真正 ARQ worker 的执行 owner 由数据库核对：同用户命令由不同 worker 并行；SIGKILL 其中一个后，加速它所属 Run 租约过期，活着的 worker 将 Run 收敛为 worker_lease_expired 并清理原命令。无关 Run 的文件完成，恢复后的新 Run 继续产生文件。
- Compose 全 unit 2575 passed、55 skipped；Schema 18 passed；Cube integration 10 passed（包含 45 秒休眠后软件与共享文件恢复）；两进程首次创建和终态标记清理包含在完整 integration 中，另以 Provider 启动断连后迟到请求的真实退出码和无文件写入证明取消墓碑保留；Agent E2E 与两 worker 恢复模式各 1 passed。frontend 的设置行为 2 passed，lint/build、文档 build 和工程契约检查通过；全 frontend unit 有一个既有 messageDebug 断言失败，不以设置测试替代该失败。独立 Review 批准当前文件、生命周期、取消及测试资源清理。API/worker 重启已实测重连原 VM，Home 配置与最新 Workspace 文件保留。

旧能力不存在：shipping 不再引用旧 provisioner、agent-sandbox SDK、Docker socket 或双 provider 配置，实验包已删除。CI 与部署使用 Cube，原生 KVM CI runner 尚未执行，不作为验证通过。

重新引入条件：需要新的部署兼容承诺且有明确 consumer 时另行裁决，不以自动 fallback 引入。

### 用户环境变量生效验证

真实 HTTP、PostgreSQL、Cube 验证新值进入新命令、旧进程保留旧值、删除变量影响后续命令、休眠恢复同一实例、shell 覆盖时不匹配、响应无变量值，以及验证期间配置更新返回 409。完整 Cube integration 13 passed；真实双 worker 取消与崩溃恢复 E2E 1 passed；后端 unit 2575 passed、55 skipped。

设置组件相关 3 项通过，含 deferred 验证与刷新交错、编辑失效、其他页面配置更新和失败反馈；真实页面验证保存与匹配结果、环境状态刷新和禁用重启。前端 lint/build、文档 build 与工程契约检查通过。全前端 unit 585 passed、1 个既有 messageDebug 断言失败，不将该失败记为通过。独立 Review 批准增量，原生节点重启恢复、压力与远程 CI 不在本轮验证范围。

### 嵌套 Cube 共享挂载恢复与压力验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 当前共享通道可执行命令和文件操作 | FUSE 等待、命令超时 | run-vm.sh 与实际执行节点 | 无 D 状态 mount；真实 sandbox integration；Agent HTTP/worker E2E | 原现场共享等待与失败命令；缺失/默认队列配置 guard 拒绝 | Passed；历史触发原因未闭合 |
| 保留原环境与数据 | 实例替换、代次变化、文件丢失 | user_sandboxes、Cube 磁盘、UserWorkspace | 两个原环境身份及代次；三轮原 Home 一致；2547 个共享文件/链接恢复后及压力后回读 | 保留路径不调用原用户 rebuild/destroy | Passed |
| fuzz 具有独立结果 oracle | 旧文件丢失被重写掩盖、输出混淆、句柄泄漏 | sandbox integration | VM 恢复前值校验、宿主哈希、真实退出码、持久状态；system-tests workflow | 缺失旧文件触发 FileNotFoundError，且不生成替代文件 | Passed |

实际执行 `backend/.venv/bin/python -m pytest backend/test/unit/config -q`（101 passed），Ruff check/format 与 `bash -n docker/cube/run-vm.sh` 通过。`docker compose exec -T api uv run --group test pytest test/unit -m 'not slow' -q` 为 2582 passed、59 skipped；容器缺少仓库根目录，配置用例由宿主测试覆盖。

`docker compose exec -T api uv run --group test pytest test/integration/sandbox -q -o junit_family=legacy --junitxml=/tmp/cube-final-sandbox.xml` 为 20 passed（121.74 秒），`docker compose exec -T api uv run --group test pytest test/e2e/test_cube_agent_execution_e2e.py -q` 为 1 passed（39.56 秒），验证真实节点与最终产物。

先执行独立 fuzz，再运行整个 sandbox 集合及五轮独立重放，最后运行增强旧值 oracle 后的完整集合，累计八轮、48 个压力场景加一个缺失旧文件负向场景：5760 次 fsync/原子替换、512 条随机命令。4 vCPU/8 GiB 的本机嵌套 KVM 上，8 并发、1 MiB 文件组批量写入为 79.173～96.613 MiB/s，命令组内延迟中位数为 527.623～581.592 毫秒。恢复前值校验只在最后一轮增强集合中执行；前七轮仍有宿主回读及原 integration 的文件保留验证。吞吐分母包含命令建立、VM 回读及收尾，结果用于拓扑内回归，不作为生产容量阈值。

原配置隔离对照只操作离线只读备份上的新 overlay 与独立共享目录：8 并发完成 1024 次 1 MiB fsync/替换/回读，6 个测试 VM 完成 create/pause/resume/read/delete，4 并发完成 80000 次无缓存 stat，均未阻塞。副本资源与 CPU/内存规格和在线节点不同，只能说明该短时负载未重现故障。

`python3 scripts/verify_engineering_contracts.py` 通过（194 decisions、345 docs），`python3 -m unittest scripts.test_verify_engineering_contracts` 为 65 passed；`cd docs && pnpm run build`、配置解析、Shell 语法及 staged/unstaged diff 检查通过。新增沙盒测试 Ruff check/format 通过；工程脚本 Ruff 检出两处修改前就存在的 ISC004，保留原代码，忽略这两处既有规则后其他检查通过。全新独立 Reviewer 未发现本轮功能、隔离或 oracle 阻断问题；私有备份与原用户现场证据由修复负责人回读验证，Reviewer 没有读取敏感数据。

未执行远程专用 runner、原生节点重启恢复或持续数小时的 soak。后续通知丢失隔离实验及尚未复现的准确 QEMU 竞态由[事故复盘](../../postmortems/2026-10-15-cube-shared-mount-stall.md#通知丢失的隔离验证)记录。工作流配置由工程契约检查约束，负向测试将整个子树退回旧单文件命令时必须拒绝，防止漏跑共享挂载 fuzz；文件、数据库和协议结果拥有业务验证事实。

### 用户环境磁盘用量简要查询

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 数值来自当前用户真实环境 | 错误磁盘、凭据泄漏、假零值 | provider 与 Web 认证路由 | 真实 HTTP 与独立 df oracle、句柄回读；3 个相关 integration 通过 | 未登录拒绝；失败/非法数值拒绝；11 个磁盘 unit 通过 | Passed |
| 手动操作与范围清楚 | 周期唤醒、重建后旧数据、误算共享目录 | settings 卡片 | 7 个目标 DOM unit、真实 Vite 组件、lint/build | 自动刷新不查询磁盘；失败、代次变化及断线作废旧值与在途请求 | Passed |

在空闲窗口显式重启 API 应用路由，readiness 恢复。真实集成执行 `docker compose exec -T api uv run --group test pytest test/integration/sandbox/test_cube_user_environment.py -k 'disk_usage or sleep_timestamps or http_status_rebuild' -q`：3 passed。磁盘场景先用 `df -B1` 读取独立计数，再暂停测试 VM；状态查询保持休眠，显式 POST 恢复原 VM 与同一代次，返回数值在 1 MiB 的文件系统波动范围内一致，持久命令句柄为零。该场景不遍历或清理其他用户资源。

`docker compose exec -T api uv run --group test pytest test/e2e/test_cube_agent_execution_e2e.py -q`：1 passed；`pytest test/unit -m 'not slow' -q` 同样通过 API 容器的 `uv run --group test` 执行：2593 passed、59 skipped。新增磁盘边界 unit 11 passed，目标设置 DOM unit 7 passed。后端修改源码和相关测试 Ruff check 通过。

全新独立 Reviewer 发现状态断线使代次比较失效，修正为状态失败时同步清理和作废磁盘快照；两种断线跨代次的 DOM 负向案例覆盖已有数值和在途响应。复审未发现其他必须修正问题。

最终执行 `docker compose exec -T frontend pnpm run lint:check`、`pnpm run test:unit`（591 passed）及 `pnpm run build` 均通过。`playwright-cli -s=sandbox-disk run-code --filename=frontend/test/browser/sandboxTiming.js` 的十种场景通过，包含自动状态刷新不查询磁盘、手动查询一次、失败清除旧值；实际 Vite 组件使用固定响应 fixture，真实服务语义由上述 HTTP integration 独立验证。截图覆盖浅/深色及 1440/1024/768px，计时与关闭清理场景同时通过，运行截图未入库。

工程契约检查（196 decisions、347 docs）、其 65 项 unittest、文档构建及 staged/unstaged diff 检查通过。未验证原生远程节点或宿主物理空间分摊。

### 用户环境休眠时间与空闲倒计时

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 时间来自真实状态，不改变原实例 | 从活动时间猜测休眠，恢复后残留旧时间 | provider 与 user_sandboxes | 真实 HTTP、命令、休眠恢复和数据库回读；sandbox 21 passed | 外部暂停后维护仍为 null；忙碌无截止时间；恢复清除时间 | Passed |
| 展示时间、时长、倒计时并诚实显示边界 | 时钟偏差、忙碌倒计时、零值假休眠 | settings 卡片 | DOM unit、lint/build、真实 Vite 浏览器组件、浅/深色和 1440/1024/768px 截图 | 旧时间未知、任务忙碌、阈值到达、读取失败、旧查询与重建交错 | Passed |

数据库初始化及其验证由[首个 Schema 基线决策](2026-10-19-schema-baseline-one.md)取代；下述迁移命令和运行结果仅保留为历史验证记录。

实际执行 `docker compose exec -T api uv run --group test pytest test/integration/services/test_schema_migration_version.py -q`（19 passed）、`pytest test/integration/sandbox -q`（21 passed）、`pytest test/e2e/test_cube_agent_execution_e2e.py -q`（1 passed）及 `pytest test/unit -m 'not slow' -q`（2582 passed、59 skipped）。全部 pytest 命令在 API 容器内通过 `uv run --group test` 执行。

`node --test test/unit/userSandboxSettings.test.js` 的五项 DOM 用例通过，包含恢复后的旧请求不能覆盖状态、重建刷新交错不遗留 loading。`docker compose exec -T frontend pnpm run lint:check`、`pnpm run test:unit`（589 passed）和 `pnpm run build` 通过。后端修改源码及沙盒测试 Ruff check 通过；迁移测试文件有九处修改前已存在的 E501，新增代码没有 E501，其余规则检查通过。

空闲窗口停止 API/worker，以 `docker compose run --rm --no-deps schema-init` 应用迁移，再显式启动两服务。私有数据库回读确认两个原环境的身份、代次、凭据和活动时间保持，原休眠记录仍未知；一个原运行环境由正常 worker 空闲维护进入休眠并记录确认时间。readiness 恢复，未重建或销毁原 VM。

`playwright-cli -s=sandbox-timing-verified run-code --filename=frontend/test/browser/sandboxTiming.js` 通过八种场景：运行倒计时、本地每秒更新不增加请求、自动刷新到休眠、旧时间未知、忙碌、阈值到达、读取失败及卸载。组件在真实 Vite 页面运行，使用应用的主题 Provider；状态响应使用固定 fixture，真实服务语义由上述 PostgreSQL/HTTP integration 独立验证。卸载后等待超过一次轮询周期，确认无新请求且两个定时器已清理。截图覆盖浅色、深色、1440/1024/768px 和错误边界，运行产物未入库。

工程契约检查通过（195 decisions、346 docs），其 unittest 为 65 passed；文档构建及 staged/unstaged diff 检查通过。全新独立 Reviewer 发现的重建刷新交错、已休眠时间误记和测试维护资源隔离问题均由对应负向用例闭合；私有原环境回读由修复负责人验证，Reviewer 不读取敏感数据。低于 768px 不属于项目适配验收范围；未执行远程部署验证。

### 后端装配与旧执行路径的证据边界

装配定向测试验证显式 scope、Workdir、惰性创建与产物根，缺失 scope 的 None、空字符串和空白字符串负向案例在旧实现失败、修复后通过。旧后端阶段相关集合 206 passed，真实父子 Agent 与附件持久化 E2E 2 passed；隔离合并副本定向集合 483 passed，全量 2305 passed、1 failed，失败为已在基线复现的 Skill 工具挂载案例。该阶段全量 gate 未通过，不用定向成功替代；当前 Cube 的生命周期证据见本节前述真实链路。

旧 AIO 1.11.0 的原生 grep 验证曾在无用户挂载的隔离实例通过真实 HTTP、文件与模型 ToolMessage，覆盖字面量、特殊文件名、glob、多根限额、缺失根、隐藏文件、长行、输出上限和原生链接行为；完整 API/worker E2E 未执行。这些结果只证明已删除的 AIO 路径，不证明当前 Cube 搜索具有相同语义。旧 CI 镜像预拉取只完成实现核对，实际业务结果未另行验证；当前远程 Cube runner 仍不计为通过。

旧能力不存在：shipping 不再依赖 AIO grep API、provisioner 镜像预拉取、独立 composite 模块或 runtime scope 到 checkpoint thread 的回退。

重新引入条件：有明确的新部署或文件路由消费者，并以当前权限、生命周期和真实产物证据重新裁决，不引入自动 fallback。

### 模板工具链、浏览器与远程安装

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 普通用户默认 Node、Python、uv 可运行并创建项目环境 | root 构建成功但用户 PATH、版本或写权限错误 | Dockerfile.user / Cube executor | 版本命令、Node 脚本、uv venv 与依赖导入、宿主 JSON 回读 | 旧模板缺少 python、uv、uvx，同一验收失败 | Passed |
| 基础 CLI 和 Python 包能处理真实输入 | 仅检查命令路径，业务能力不可用 | Dockerfile.user | 本地 HTTP、JSON、搜索、压缩、图像和工作簿原始 XML 回读 | 旧工具链模板因缺少 httpx 在导入时失败 | Passed |
| 普通用户可启动配套 Chromium，中文网页与下载可用 | root 缓存、浏览器版本或系统库错误 | 模板 / Playwright 安装器 | 开启 Chromium sandbox 的截图、下载与 PDF 产物回读 | 缓存指向不存在目录时明确拒绝启动 | Passed |
| Poppler 能提取生成 PDF 的文本与页面 | 只检查 PDF 文件头 | 模板 / poppler-utils | pdfinfo、pdftotext、pdftoppm 与固定文本、页图像断言 | 未单独执行 Poppler 缺失负控 | Passed |
| 含链接的临时目录可清理且保留外部目标与其他请求 | 链接拒绝安装，或跟随链接误删 | Workspace / remote.py | 文件系统与安装 unit 回读真实文件；CLI 临时 HOME 链接 | 外部文件、目录、悬空链接与链接父目录；恢复原拒绝条件后真实安装失败 | Passed |
| 真实安装产生可读取的个人技能 | 仅 mock 下载成功，实际未发布 | personal.py | grill-me 个人列表、SKILL.md、空临时目录与保留文件回读 | 旧 Node 12 / 缺失 Git 导致 CLI 失败 | Passed |
| 工具链与 AIO 比较可追溯 | 把未公开的版本当成核实事实 | runtime_paths.py / 官方资料 | 源码、官方文档和镜像元数据 | 不推断未公开版本或未经实测的 Shell 默认值 | Inspected |

实际验证记录：

- Cube 节点按 `docker/cube/Dockerfile.user` 构建并注册独立模板。最终浏览器工具模板构建通过；Node 24.20.0、npm 11.19.0、Python 3.13.15、pip 26.2.1、uv/uvx 0.12.6；Playwright 安装配套 Chromium 156.0.8078.4、revision 1248 及 headless shell。
- `docker compose exec -T api uv run --group test pytest test/unit/workspace test/unit/services/skills test/unit/toolkits/test_install_skill.py -q`：209 passed。原符号链接删除实现的最小相关集合为 7 failed、44 passed，失败均来自链接拒绝。
- 独立模板真实安装 `mattpocock/skills` 的 `grill-me` 通过。独立测试进程恢复原符号链接拒绝条件后，同一安装测试为 1 failed，工具返回 `安装异常：symlink paths are not allowed`，堆栈定位 npm 缓存 `.bin/add-skill`。该负控不修改在线进程或用户文件。
- 工具链测试以旧 Node 22、系统 Python 3.10 模板为负控：1 failed，缺少默认 `python`、`uv` 与 `uvx`；浏览器工具测试以未预装包的旧模板为负控：1 failed，直接原因为缺少 `httpx`，未执行到浏览器阶段。新模板普通用户将缓存指向不存在目录时，浏览器非零退出并返回 `Executable doesn't exist`。
- `docker compose exec -T -e CUBE_TEMPLATE_ID=yuxi-browser-tools-test api uv run --group test pytest test/integration/sandbox/test_cube_preinstalled_tools.py test/integration/sandbox/test_cube_runtime_toolchain.py test/integration/sandbox/test_remote_skill_install.py -q -o cache_dir=/tmp/pytest-browser-tools --basetemp=/tmp/pytest-yuxi-browser-tools`：3 passed。
- 普通用户启用 `chromium_sandbox=True`，宿主回读 900×600 中文网页截图、PDF 文本与 Poppler 页图像、下载字节、zip 解压结果、Pillow 图像和工作簿 XML；人工查看截图与 PDF 页图像，中文清晰可读。PyMySQL 验证导入与字符串转换，未连接真实 MySQL。
- `docker compose exec -T api uv run --group test pytest test/unit -m "not slow" -q`：2629 passed、59 skipped；跳过项为容器未挂载仓库根目录的既有配置检查。工程契约、65 项 verifier 自测、相关 Python Ruff lint/format、文档构建与 `git diff --check` 通过，独立审查通过。

### 默认模板切换与服务重启

默认 `yuxi-user` 别名独立回读的 templateID 与已验收模板精确相等。9 个常驻 Compose 服务的 StartedAt 均更新且处于 running，8 个配置 healthcheck 的服务均 healthy，API ready 与前端 HTTP 返回 200；两个已有用户 VM 的数据库身份、代次和实例引用指纹保持不变。

`docker compose exec -T api uv run --group test pytest test/integration/sandbox/test_cube_preinstalled_tools.py test/integration/sandbox/test_cube_runtime_toolchain.py test/integration/sandbox/test_remote_skill_install.py -q -o cache_dir=/tmp/pytest-default-template --basetemp=/tmp/pytest-yuxi-default-template`：3 passed，15.55 秒。未覆盖 `CUBE_TEMPLATE_ID`，验证默认别名的实际工具与安装产物。重启期间 Redis 的持久化恢复与未闭合根因见[事故记录](../../postmortems/2026-10-15-redis-aof-restart.md)。

模板验收覆盖新建真实用户 VM、内置安装工具与最终产物；已有 VM 仅核对身份保持，未升级其工具链。该模板切换未复验 Yuxi 浏览器聊天或 worker Run；前述 Cube 生命周期 E2E 是独立范围的历史结果。浏览器网页使用本地 HTTP fixture，未验证特定外部站点。AIO 仅核对官方文档和镜像元数据，未运行其容器。
