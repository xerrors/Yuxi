import { apiGet, apiPut } from './base'

const AGENT_ENV_PATH = '/api/user/agent-env'

export const agentEnvApi = {
  get: () => apiGet(AGENT_ENV_PATH),

  update: (env) => apiPut(AGENT_ENV_PATH, { env: env || {} })
}
