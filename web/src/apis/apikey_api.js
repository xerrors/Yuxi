import { apiSuperAdminGet, apiSuperAdminPost, apiSuperAdminPut, apiDelete } from './base'

export const apikeyApi = {
  list: (skip = 0, limit = 100) => apiSuperAdminGet('/api/apikey/', { params: { skip, limit } }),

  create: (data) => apiSuperAdminPost('/api/apikey/', data),

  get: (id) => apiSuperAdminGet(`/api/apikey/${id}`),

  update: (id, data) => apiSuperAdminPut(`/api/apikey/${id}`, data),

  delete: (id) => apiDelete(`/api/apikey/${id}`),

  regenerate: (id) => apiSuperAdminPost(`/api/apikey/${id}/regenerate`)
}
