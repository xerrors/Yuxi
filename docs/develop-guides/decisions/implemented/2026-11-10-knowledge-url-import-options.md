# 安全设置集中维护外部来源白名单

状态：implemented
类型：feature
Owner：backend/yuxi/modules/knowledge/options.py

## 问题

知识库 URL 导入只读取部署环境中的域名白名单，管理员无法像远程 Skill 来源一样通过设置启用或关闭导入。修改部署环境还需要重建 API 容器。两项外部来源策略需要一个独立的安全设置入口。

## 决策

### 实现方案

知识域定义 `knowledge_url_import_policy.allowed_hosts`，bootstrap 按现有机制同步 Option 定义，超级管理员通过现有 Options API 保存到 PostgreSQL。设置弹窗增加独立的“安全设置”页，同时承载 URL 导入表单与现有 Skill 来源表单；基本设置移除 Skill 表单，沿用其存储键和规则。桌面和移动导航仅向超级管理员展示，页面沿用现有惰性挂载和草稿保留；不新增通用设置框架或专属 API。

新请求的抓取器读取 Option，以本次读取的列表校验原始 URL 和每次重定向。显式保存空列表关闭导入；未保存时沿用 `YUXI_URL_WHITELIST` 的逗号列表，否则默认关闭。通用 Option 解析支持列表环境变量，表单响应增加不含敏感字段的有效值，避免把部署中的实际白名单展示为空；原始 `value` 契约保留。配置提交和 Redis 缓存失效复用现有服务，不新增进程内缓存。

目标：超级管理员可在页面维护导入域名，下一次请求消费保存后的值；普通管理员和用户不能修改。非目标：不自动允许用户提供的网址，不修改 Skill 策略、Agent 网页供应商、DNS/SSRF、类型/大小限制和文件暂存流程。

## 替代方案

仅保留环境变量无法满足页面配置需求。写环境文件会混用部署状态和业务设置。为知识库建立单独配置 API、存储或缓存会重复现有 Options 能力。将列表环境变量提前迁移为数据库值会增加一次性持久化逻辑，并改变部署默认值的优先级。

## 后果

沿用现有非敏感 Option 缓存承诺：正常提交后失效，失效失败记录日志，旧值最多保留到 300 秒 TTL。进行中的抓取沿用开始时的白名单，不追溯撤销。部署升级需先通过 app-init 同步新定义，再重启 API 加载代码；管理员配置本身无需重启。

这项配置取代[错误提示修复](./2026-11-10-knowledge-url-import-errors.md)中仅通过部署环境启用导入的限制，错误的公开消息和脱敏边界继续由抓取器拥有。Skill 原有的精确匹配策略不变，知识库仍允许域名和子域名；两者共享管理入口，不共享来源规则。

## 验证

- `test/integration/api/test_knowledge_url_import_options.py` 使用隔离 PostgreSQL、独立 Redis 缓存键和真实 TCP HTTP。独立回读配置记录和更新者归属，验证保存后的实际抓取字节、受限重定向、缓存刷新，以及环境变量存在时显式空列表仍关闭导入；匿名、普通用户和普通管理员的写入被拒绝且数据库不变。
- `test/unit/config/test_options.py` 验证定义注册、默认关闭、逗号列表环境值和显式空列表优先级；`effective_value` 不含敏感字段。恢复原实现时，列表解析和有效值投影的两个负向案例失败。
- `test/unit/knowledge/test_url_fetcher.py` 与错误信息边界 HTTP integration 保留私网、DNS 钉扎、重定向、内容限制与错误脱敏验证；精确和通配白名单保留末尾点域名的既有语义。相关后端 unit 71 项、HTTP integration 39 项通过，新 HTTP 文件由现有 service-integration 入口执行。
- 前端真实组件 unit 验证有效值、保存失败保留草稿和空列表重载；真实 SettingsModal 模板验证惰性挂载、切页保留草稿、普通角色请求 `security` 初始页仍无法挂载。`test/browser/urlImportSettings.js` 通过完整设置弹窗 DOM 验证 URL 与 Skill 两项入口、编辑/取消/保存/重载/错误恢复和角色可见性，检查 1440px、1024px 浅色及 768px 深色布局。前端 lint 与生产构建通过。

浏览器设置交互使用固定 HTTP 响应，持久化和后端权限结论来自独立 HTTP/数据库证据。HTML 抓取成功不代表完整解析或入库，本文不扩大该验证范围。
