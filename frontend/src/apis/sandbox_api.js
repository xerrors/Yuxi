import { apiGet, apiPost } from './base'

export const sandboxApi = {
  get: () => apiGet('/api/user/sandbox'),
  rebuild: (failedGeneration) => apiPost(
    failedGeneration == null ? '/api/user/sandbox/rebuild' : `/api/user/sandbox/rebuild?failed_generation=${failedGeneration}`
  ),
  diskUsage: () => apiPost('/api/user/sandbox/disk-usage')
}
