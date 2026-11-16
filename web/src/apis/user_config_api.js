import { apiGet, apiPut } from './base'

const USER_CONFIG_PATH = '/api/user/config'

export const userConfigApi = {
  get: () => apiGet(USER_CONFIG_PATH),

  update: (config) => apiPut(USER_CONFIG_PATH, config)
}
