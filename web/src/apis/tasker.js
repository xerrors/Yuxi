import { apiAdminDelete, apiAdminGet, apiAdminPost } from './base'

const BASE_URL = '/api/tasks'

export const taskerApi = {
  fetchTasks: async (params = {}) => {
    const query = new URLSearchParams(params).toString()
    const url = query ? `${BASE_URL}?${query}` : BASE_URL
    return apiAdminGet(url)
  },

  fetchTaskDetail: async (taskId) => {
    return apiAdminGet(`${BASE_URL}/${taskId}`)
  },

  cancelTask: async (taskId) => {
    return apiAdminPost(`${BASE_URL}/${taskId}/cancel`, {})
  },

  deleteTask: async (taskId) => {
    return apiAdminDelete(`${BASE_URL}/${taskId}`)
  }
}
