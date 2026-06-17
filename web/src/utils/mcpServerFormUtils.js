import { parseMcpJsonText } from './mcpConnectionUtils.js'

export const createMcpServerFormState = (data = null) => ({
  name: data?.name || '',
  description: data?.description || '',
  transport: data?.transport || 'streamable_http',
  url: data?.url || '',
  command: data?.command || '',
  args: Array.isArray(data?.args) ? data.args : [],
  env: data?.env || null,
  headersText: data?.headers ? JSON.stringify(data.headers, null, 2) : '',
  authConfigText: data?.auth_config ? JSON.stringify(data.auth_config, null, 2) : '',
  timeout: data?.timeout ?? null,
  sse_read_timeout: data?.sse_read_timeout ?? null,
  tags: Array.isArray(data?.tags) ? data.tags : [],
  icon: data?.icon || ''
})

export const stringifyMcpServerConfig = (data) => (data ? JSON.stringify(data, null, 2) : '')

export const formatMcpServerJsonContent = (text, onError) => {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    onError?.('JSON 格式错误，无法格式化')
    return undefined
  }
}

export const parseMcpServerJsonContent = (text, onError) => {
  try {
    return JSON.parse(text)
  } catch {
    onError?.('JSON 格式错误')
    return undefined
  }
}

export const buildMcpServerPayloadFromForm = (form, onError) => {
  const headers = parseMcpJsonText(form.headersText, '请求头', { onError })
  if (headers === undefined) return null

  const authConfig = parseMcpJsonText(form.authConfigText, '认证配置', { onError })
  if (authConfig === undefined) return null

  return {
    name: form.name,
    description: form.description || null,
    transport: form.transport,
    url: form.url || null,
    command: form.command || null,
    args: form.args.length > 0 ? form.args : null,
    env: form.env,
    headers,
    auth_config: authConfig,
    timeout: form.timeout || null,
    sse_read_timeout: form.sse_read_timeout || null,
    tags: form.tags.length > 0 ? form.tags : null,
    icon: form.icon || null
  }
}

export const validateMcpServerPayload = (data, onError) => {
  if (!data.name?.trim()) {
    onError?.('MCP 名称不能为空')
    return false
  }
  if (!data.transport) {
    onError?.('请选择传输类型')
    return false
  }
  if (['sse', 'streamable_http'].includes(data.transport) && !data.url?.trim()) {
    onError?.('HTTP 类型必须填写 MCP URL')
    return false
  }
  if (data.transport === 'stdio' && !data.command?.trim()) {
    onError?.('StdIO 类型必须填写命令')
    return false
  }
  return true
}
