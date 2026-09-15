import { apiGet, apiPost, buildQuery } from './base'

export const personalTrashApi = {
  list: (page = 1) => apiGet(`/api/personal-trash?${buildQuery({ page, page_size: 20 })}`),
  restore: (id) => apiPost(`/api/personal-trash/${encodeURIComponent(id)}/restore`, {}),
  retry: (id) => apiPost(`/api/personal-trash/${encodeURIComponent(id)}/retry`, {})
}
