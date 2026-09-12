import { apiGet, apiPost, apiPut } from './base'

const root = '/api/counseling/students'

export const counselingApi = {
  listStudents: () => apiGet(root),
  listCounselors: () => apiGet(`${root}/counselors`),
  getStudent: (id) => apiGet(`${root}/${encodeURIComponent(id)}`),
  createStudent: (payload) => apiPost(root, payload),
  updateStudent: (id, payload) => apiPut(`${root}/${encodeURIComponent(id)}`, payload)
}
