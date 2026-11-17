import { apiGet } from './base'

export const devicesApi = {
  list: () => apiGet('/api/devices')
}
