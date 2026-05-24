import { apiGet, apiPost, apiPut, apiDelete } from './base'

export const scheduleApi = {
  list: (params) => apiGet('/api/schedules', { params }),

  create: (data) => apiPost('/api/schedules', data),

  get: (id) => apiGet(`/api/schedules/${id}`),

  update: (id, data) => apiPut(`/api/schedules/${id}`, data),

  delete: (id) => apiDelete(`/api/schedules/${id}`),

  trigger: (id) => apiPost(`/api/schedules/${id}/trigger`),

  listLogs: (id, params) => apiGet(`/api/schedules/${id}/logs`, { params })
}
