/**
 * API模块索引文件
 * 导出所有API模块，方便统一引入
 */

// 导出API模块
export * from './system_api' // 系统管理API
export * from './knowledge_api' // 知识库管理API
export * from './workspace_api' // 用户个人工作区
export * from './schedule_api' // 定时任务 API
export * from './graph_api' // 图谱API
export * from './agent_api' // 智能体API
export * from './tasker' // 任务管理API
export * from './mindmap_api' // 思维导图API
export * from './department_api' // 部门管理API
export * from './mcp_api' // MCP API
export * from './skill_api' // Skills API
export * from './subagent_api' // SubAgent API
export * from './tool_api' // 工具 API
export * from './mention_api' // 提及搜索 API

// 导出基础工具函数
export {
  apiGet,
  apiPost,
  apiPut,
  apiDelete,
  apiAdminGet,
  apiAdminPost,
  apiAdminPut,
  apiAdminDelete,
  apiSuperAdminGet,
  apiSuperAdminPost,
  apiSuperAdminPut,
  apiSuperAdminDelete
} from './base'

/**
 * API模块说明:
 *
 * 1. system_api.js: 系统管理API
 *    - 健康检查、配置管理、信息管理、OCR服务
 *    - 权限要求: 部分公开，部分需要管理员权限
 *
 * 2. knowledge_api.js: 知识库管理API
 *    - 数据库管理、文档管理、查询接口、文件管理
 *    - 权限要求: 管理员权限
 *
 *
 * 4. graph_api.js: 图谱API
 *    - 知识图谱相关功能
 *
 * 5. tools.js: 工具API
 *    - 工具信息获取
 *
 * 6. agent.js: 智能体API
 *    - 智能体管理、聊天、配置等功能
 *
 * 注意：API模块已处理权限验证和请求头，使用时无需再手动添加认证头
 */
