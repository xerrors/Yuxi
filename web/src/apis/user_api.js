import { apiAdminGet } from './base'

export const userApi = {
  getUsers: () => apiAdminGet('/api/auth/users')
}
