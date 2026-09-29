# API Key 创建请求 ID 支持 HTTP 访问

状态：archived
类型：bug-fix
Owner：web/src/components/ApiKeyManagementComponent.vue

## 问题

普通 HTTP 地址中的浏览器不提供 `crypto.randomUUID()`，API Key 创建弹窗因此在发出请求前抛异常。客户端请求 ID 是创建幂等标识，服务端 `user_router.py` 接受长度为 8–64 的字母、数字及指定分隔符字符串。

## 决策

弹窗使用 `crypto.getRandomValues()` 产生 16 字节随机值，编码为 32 位十六进制请求 ID。已有 sessionStorage 请求 ID 原样复用；失败重试保留 ID，成功或取消后清除。API Key secret 仍由服务端拥有。

## 替代方案

要求 HTTPS 能解决浏览器 API 限制，但不能覆盖现有 HTTP 部署。新增 UUID 库或保留两种生成路径没有当前格式契约上的必要性。时间戳与 Math.random 的组合不如浏览器随机源可靠。

## 后果

请求 ID 保留 128 位随机熵，符合现有接口与数据库约束，不引入依赖或迁移。部署仍需支持 Web Crypto 的现代浏览器。该修复完整收敛于弹窗的 ID 生成，没有待裁决的接口或持久化变化，因此直接记录为 implemented。

## 验证

`web/test/unit/apiKeyManagement.test.js` 在没有 randomUUID 的环境执行真实组件脚本，验证弹窗、请求载荷、失败重试、成功清理和已有 UUID 的兼容。恢复直接调用 randomUUID 时，测试因原始 TypeError 失败。运行命令：`docker compose exec web pnpm run test:unit`。浏览器验证负责确认普通 HTTP 环境中的真实 DOM 点击行为；该前端修复不改变后端密钥派生与持久化语义。
