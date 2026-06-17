const SECRET_FIELD_LABELS = {
  client_id: 'Client ID',
  client_secret: 'Client Secret',
  access_token: 'Access Token',
  refresh_token: 'Refresh Token',
  issuer_url: 'Issuer URL',
  api_key: 'API Key'
}

export const MCP_CONNECTION_SCOPE_LABELS = {
  inline: '内联',
  system: '全局共享',
  department: '部门共享',
  user: '个人专用'
}

export const MCP_CONNECTION_STATUS_LABELS = {
  active: '启用',
  disabled: '停用',
  reauth_required: '需要重连',
  invalid: '无效'
}

export const getMcpSecretFieldLabel = (fieldName) => SECRET_FIELD_LABELS[fieldName] || fieldName

export const parseMcpJsonText = (text, label, { allowRawString = false, onError } = {}) => {
  const trimmed = String(text || '').trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    if (allowRawString) {
      return trimmed
    }
    onError?.(`${label} JSON 格式错误`)
    return undefined
  }
}

export const setNestedSecretValue = (target, path, value) => {
  const segments = String(path || '')
    .split('.')
    .filter(Boolean)
  let current = target
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i]
    if (segment === '__proto__' || segment === 'constructor' || segment === 'prototype') {
      return
    }
    if (i === segments.length - 1) {
      current[segment] = value
    } else {
      current[segment] = current[segment] || {}
      current = current[segment]
    }
  }
}

export const buildMcpCredentialFromForm = ({
  credentialText = '',
  secretValues = {},
  isEditing = false,
  emptyCreateValue = null,
  rawLabel = '长期凭据',
  onError
} = {}) => {
  const rawCredential = parseMcpJsonText(credentialText, rawLabel, {
    allowRawString: true,
    onError
  })
  if (rawCredential === undefined) return undefined
  if (rawCredential !== null) return rawCredential

  const secrets = {}
  Object.entries(secretValues || {}).forEach(([key, value]) => {
    const trimmedValue = String(value || '').trim()
    if (trimmedValue) {
      setNestedSecretValue(secrets, key, trimmedValue)
    }
  })

  if (Object.keys(secrets).length === 0) {
    return isEditing ? undefined : emptyCreateValue
  }
  return { secrets }
}

export const validateMcpCredentialFields = ({
  isEditing = false,
  secretFields = [],
  secretValues = {},
  credentialText = '',
  onError
} = {}) => {
  if (isEditing || secretFields.length === 0 || String(credentialText || '').trim()) {
    return true
  }

  const missingFields = secretFields.filter(
    (fieldName) => !String(secretValues[fieldName] || '').trim()
  )
  if (missingFields.length === 0) {
    return true
  }
  onError?.(`请填写凭据字段：${missingFields.join('、')}`)
  return false
}

export const isMcpConnectionCredentialMissing = (connection, credentialsRequired) =>
  Boolean(credentialsRequired && !connection?.has_credentials)

export const canToggleMcpConnectionStatus = (
  connection,
  { isScopeMatched = () => true, isCredentialMissing = () => false } = {}
) => {
  if (!['active', 'disabled'].includes(connection?.status)) return false
  if (connection.status === 'disabled') {
    return isScopeMatched(connection) && !isCredentialMissing(connection)
  }
  return true
}

export const getMcpConnectionStatusSwitchLabel = (connection, getStatusLabel) =>
  connection?.status === 'active' ? '已启用' : getStatusLabel(connection?.status)

export const getMcpConnectionStatusToggleTooltip = (
  connection,
  {
    isScopeMatched = () => true,
    isCredentialMissing = () => false,
    authBindingScopeLabel = '未限定'
  } = {}
) => {
  if (connection?.status === 'active') return '停用连接'
  if (connection?.status === 'disabled') {
    if (!isScopeMatched(connection)) {
      return `该连接未生效，不能启用；当前 MCP 使用${authBindingScopeLabel}`
    }
    return isCredentialMissing(connection) ? '请先补充凭据' : '启用连接'
  }
  return '请先重连或编辑凭据'
}

export const canRunMcpConnectionAction = (
  connection,
  { isScopeMatched = () => true, isCredentialMissing = () => false } = {}
) => isScopeMatched(connection) && !isCredentialMissing(connection)

export const getMcpConnectionActionTooltip = (
  connection,
  enabledText,
  scopeMismatchText,
  { isScopeMatched = () => true, isCredentialMissing = () => false } = {}
) => {
  if (canRunMcpConnectionAction(connection, { isScopeMatched, isCredentialMissing })) {
    return enabledText
  }
  return isCredentialMissing(connection) ? '请先补充凭据' : scopeMismatchText
}

const createConnectionIssue = (key, label, description, actionLabel, tone) => (
  { key, label, description, actionLabel, tone }
)

export const getMcpConnectionIssue = (
  connection,
  {
    includeScopeMismatch = true,
    isScopeMatched = () => true,
    isCredentialMissing = () => false,
    authBindingScopeLabel = '未限定',
    missingCredentialsDescription = '缺少长期凭据，运行时无法换取或注入 token。',
    reauthRequiredDescription = '缓存 token 已失效，需要重新授权后才能继续使用。',
    testFailedDescription = '最近一次连接检测失败。'
  } = {}
) => {
  if (!connection) return null
  if (includeScopeMismatch && !isScopeMatched(connection)) {
    return createConnectionIssue(
      'scope_mismatch',
      '范围不匹配',
      `当前 MCP 使用${authBindingScopeLabel}，这组连接不会在运行时生效。`,
      '新建匹配连接',
      'warning'
    )
  }
  if (isCredentialMissing(connection)) {
    return createConnectionIssue(
      'missing_credentials',
      '缺少凭据',
      missingCredentialsDescription,
      '补充凭据',
      'error'
    )
  }
  if (connection.status === 'reauth_required') {
    return createConnectionIssue(
      'reauth_required',
      '授权失效',
      reauthRequiredDescription,
      '重连',
      'warning'
    )
  }
  if (connection.status === 'invalid' || connection.meta_json?.last_error?.message) {
    return createConnectionIssue(
      'test_failed',
      '测试失败',
      connection.meta_json?.last_error?.message || testFailedDescription,
      '编辑凭据',
      'error'
    )
  }
  return null
}

export const formatMcpConnectionLastInfo = (connection, formatTime) => {
  if (connection.meta_json?.last_success_at) {
    return `最近成功 ${formatTime(connection.meta_json.last_success_at)}`
  }
  if (connection.updated_at) {
    return `更新于 ${formatTime(connection.updated_at)}`
  }
  return '暂无记录'
}
