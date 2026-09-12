# 回答反馈目标契约

状态：proposed
类型：bug-fix
Owner：backend/package/yuxi/services/feedback_service.py

## 问题
反馈写入口只校验归属，用户、系统、工具与助手审计记录均可进入反馈写路径；界面面向助手回答，统计排除审计类型，含义不一致。

## 提案
后端与前端实现已在独立分支建立；合并前仍需维护者确认新增422行为（隔离完整live集成已通过）。既有消息存在和会话归属校验之后，要求role为assistant且message_type不属于AUDIT_MESSAGE_TYPES，兼容历史None类型。不要求执行成功，中断但已发布的回答继续支持。非法目标返回422，先于数据库写入与外部评分。

前端隐藏审计消息反馈按钮，保留模型、复制和来源。显式role优先，历史无role使用type=ai；显式status要求finished，历史无status沿用父组件既有收尾控制。点击与模态框提交也检查目标，避免目标变化后误发请求。

## 替代方案
只限制角色会遗漏assistant/model_audit。强制role/status会破坏真实历史DTO；限制全部completed会排除中断回答。数据库约束或历史清理扩大迁移影响，本提案不采用。

## 验收标准
| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 非法目标零写入和零评分 | 角色及审计类型 | feedback_service | test_feedback_service.py，12例 | user/system/tool及审计 | Passed |
| 合法及历史目标兼容 | text/None | feedback_service | 同上 | 403/404/409保持 | Passed |
| 路由与持久化结果一致 | HTTP/数据库 | chat路由/反馈服务 | 独立ASGITransport+PostgreSQL探针 | 5非法目标零记录 | Passed |
| 前端反馈目标一致 | 真实历史DTO及属性切换 | RefsComponent | refs_feedback_target.test.js，12静态场景及运行时交互；浏览器组件页面 | 审计、用户、loading与打开弹窗后变更 | Passed |
| 标准live集成完整执行 | 部署认证及应用启动 | chat路由 | test/integration/api/test_feedback_router.py | 7目标与重复提交 | Passed（1项循环7目标，真实登录及清理） |

## 风险
ASGI探针使用真实路由和PostgreSQL，仅替换认证身份、数据库依赖及外部评分，不证明完整登录或TCP应用启动。前端测试保留真实模板，隔离无关依赖；浏览器是组件验收页而非生产会话。后端全量2004通过，工程检查器62通过；Linux全Web320通过，Windows此前319通过/1失败（未改动PDF测试路径）。全lint在两平台均有未改动PDF测试Buffer未定义2项；本次两文件lint通过，build通过。历史数据和统计口径不调整。提案公开前需审阅完整diff，不将局部验证写成全套CI通过。

标准live通过完整TCP应用、真实登录、schema门禁与原清理fixtures；隔离PostgreSQL/Redis，provisioner采用memory后端，未验证容器沙箱执行。实际运行测试与最终暂存版仅格式差异，AST一致；服务代码字节一致。
