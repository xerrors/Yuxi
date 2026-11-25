import { apiSuperAdminGet } from './base'

/** 平台管理员的有界反馈列表与关联轮次观测。 */
export const feedbackApi = {
  list: (filters = {}) => {
    const params = new URLSearchParams({ limit: '50' })
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value)
    }
    return apiSuperAdminGet(`/api/system/agent-feedback?${params}`)
  },
  getTurnLangfuse: (feedbackId) =>
    apiSuperAdminGet(`/api/system/agent-feedback/${encodeURIComponent(feedbackId)}/langfuse`),
  listAgents: () => apiSuperAdminGet('/api/system/agent-feedback/agents'),
}
