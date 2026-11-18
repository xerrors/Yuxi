import { apiPost } from './base'

export const oidcProviderApi = {
  authorize: (parameters) => apiPost('/api/auth/provider/authorize', parameters)
}
