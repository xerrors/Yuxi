import { apiGet, apiPost, apiPut, apiDelete } from './base'

const API_KEY_BASE_PATH = '/api/user/apikey'

const AGENT_PUBLIC_API_ENDPOINTS = [
  { method: 'GET', path: '/api/v1/agents/', description: '列出可用智能体' },
  { method: 'POST', path: '/api/v1/agents/sessions', description: '创建会话' },
  { method: 'GET', path: '/api/v1/agents/sessions', description: '列出会话' },
  { method: 'POST', path: '/api/v1/agents/sessions/{session_id}/events', description: '提交消息或控制事件' },
  { method: 'GET', path: '/api/v1/agents/sessions/{session_id}/events', description: '订阅运行事件 (SSE)' }
]

const KNOWLEDGE_PUBLIC_API_ENDPOINTS = [
  { method: 'GET', path: '/api/v1/knowledge/tools/list_kbs', description: '列出可见知识库' },
  { method: 'POST', path: '/api/v1/knowledge/tools/query_kb', description: '检索知识库内容' },
  { method: 'POST', path: '/api/v1/knowledge/tools/search_file', description: '搜索知识库文件' },
  { method: 'POST', path: '/api/v1/knowledge/tools/open_kb_document', description: '读取知识库文档' },
  { method: 'POST', path: '/api/v1/knowledge/tools/find_kb_document', description: '定位文档内容' }
]

/**
 * 集成页对外端点。知识库路由只在 capabilities.features.knowledge === true 时挂载；
 * 未加载或非 true 时不返回，避免先闪出当前进程没有的路径。
 */
export function publicApiEndpoints(knowledgeEnabled) {
  if (knowledgeEnabled !== true) return AGENT_PUBLIC_API_ENDPOINTS.slice()
  return AGENT_PUBLIC_API_ENDPOINTS.concat(KNOWLEDGE_PUBLIC_API_ENDPOINTS)
}

export const apikeyApi = {
  list: () => apiGet(`${API_KEY_BASE_PATH}/`),

  listApps: () => apiGet(`${API_KEY_BASE_PATH}/apps`),

  create: (data) => apiPost(`${API_KEY_BASE_PATH}/`, data),

  get: (id) => apiGet(`${API_KEY_BASE_PATH}/${id}`),

  update: (id, data) => apiPut(`${API_KEY_BASE_PATH}/${id}`, data),

  delete: (id) => apiDelete(`${API_KEY_BASE_PATH}/${id}`)
}
