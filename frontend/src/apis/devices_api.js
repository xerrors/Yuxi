import { apiGet, apiPost } from './base'

export const devicesApi = {
  list: (options) => apiGet('/api/devices', options),
  getSandboxBrowser: (options) => apiGet('/api/devices/sandbox-browser', options),
  connectSandboxBrowser: (options) => apiPost('/api/devices/sandbox-browser', {}, options)
}
