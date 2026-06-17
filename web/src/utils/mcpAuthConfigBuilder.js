export const FORM_AUTH_PROVIDERS = new Set([
  'bound_secret',
  'custom_http_token',
  'client_credentials',
  'stdio_env'
])

const TOKEN_PROVIDERS = new Set(['custom_http_token', 'client_credentials'])

const DEFAULT_RESPONSE_MAP = [
  { key: 'access_token', value: 'data.access_token' },
  { key: 'refresh_token', value: 'data.refresh_token' },
  { key: 'expires_in', value: 'data.expires_in' }
]

const DEFAULT_JSON_HEADERS = [{ key: 'Content-Type', value: 'application/json' }]

const DEFAULT_FORM_HEADERS = [
  { key: 'Content-Type', value: 'application/x-www-form-urlencoded' }
]

const DEFAULT_GATEWAY_BODY = [
  { key: 'client_id', value: '${secret.client_id}' },
  { key: 'client_secret', value: '${secret.client_secret}' },
  { key: 'user_id', value: '${context.user_id}' },
  { key: 'department_id', value: '${context.department_id}' }
]

const DEFAULT_CLIENT_CREDENTIALS_BODY = [
  { key: 'grant_type', value: 'client_credentials' },
  { key: 'client_id', value: '${secret.client_id}' },
  { key: 'client_secret', value: '${secret.client_secret}' }
]

const normalizeText = (value) => String(value ?? '').trim()

export const objectToKeyValueRows = (value, fallbackRows = [{ key: '', value: '' }]) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return fallbackRows.map((row) => ({ ...row }))
  }

  const rows = Object.entries(value).map(([key, rowValue]) => ({
    key,
    value: rowValue == null ? '' : String(rowValue)
  }))
  return rows.length > 0 ? rows : fallbackRows.map((row) => ({ ...row }))
}

export const keyValueRowsToObject = (rows) => {
  const entries = (Array.isArray(rows) ? rows : [])
    .map((row) => ({
      key: normalizeText(row?.key),
      value: row?.value == null ? '' : String(row.value)
    }))
    .filter((row) => row.key)

  if (entries.length === 0) {
    return {}
  }
  return Object.fromEntries(entries.map((row) => [row.key, row.value]))
}

const createDefaultInjectEntries = (provider) => {
  if (provider === 'stdio_env') {
    return [{ name: 'MCP_ACCESS_TOKEN', value_template: '${secret.access_token}' }]
  }
  if (provider === 'bound_secret') {
    return [{ name: 'Authorization', value_template: 'Bearer ${secret.access_token}' }]
  }
  return [{ name: 'Authorization', value_template: 'Bearer ${access_token}' }]
}

export const createDefaultAuthBuilderForm = (provider = 'none') => {
  const normalizedProvider = provider === 'none' ? 'none' : provider
  const isClientCredentials = normalizedProvider === 'client_credentials'
  const isEnvProvider = normalizedProvider === 'stdio_env'

  return {
    provider: normalizedProvider,
    bindingScope: 'department',
    manifestScope: 'binding',
    injectTarget: isEnvProvider ? 'env' : 'headers',
    injectEntries:
      normalizedProvider === 'none' ? [] : createDefaultInjectEntries(normalizedProvider),
    preRefreshSeconds: TOKEN_PROVIDERS.has(normalizedProvider) ? 300 : 0,
    retryOnceOn401: TOKEN_PROVIDERS.has(normalizedProvider),
    tokenUrl: '',
    tokenMethod: 'POST',
    tokenBodyType: isClientCredentials ? 'form' : 'json',
    tokenHeaders: isClientCredentials ? DEFAULT_FORM_HEADERS.map((row) => ({ ...row })) : DEFAULT_JSON_HEADERS.map((row) => ({ ...row })),
    tokenBodyTemplate: isClientCredentials
      ? DEFAULT_CLIENT_CREDENTIALS_BODY.map((row) => ({ ...row }))
      : DEFAULT_GATEWAY_BODY.map((row) => ({ ...row })),
    tokenResponseMap: DEFAULT_RESPONSE_MAP.map((row) => ({ ...row }))
  }
}

export const isAuthConfigSupportedByBuilder = (config) => {
  if (!config || Object.keys(config).length === 0) {
    return true
  }
  return FORM_AUTH_PROVIDERS.has(config.provider)
}

export const authConfigToBuilderForm = (config) => {
  if (!config || Object.keys(config).length === 0) {
    return createDefaultAuthBuilderForm()
  }

  const provider = FORM_AUTH_PROVIDERS.has(config.provider) ? config.provider : 'custom_http_token'
  const form = createDefaultAuthBuilderForm(provider)
  const tokenRequest = config.token_request || {}

  form.bindingScope = config.binding_scope || form.bindingScope
  form.manifestScope = config.manifest_scope || form.manifestScope
  form.injectTarget = config.inject?.target || form.injectTarget
  form.injectEntries =
    Array.isArray(config.inject?.entries) && config.inject.entries.length > 0
      ? config.inject.entries.map((entry) => ({
          name: entry.name || '',
          value_template: entry.value_template || ''
        }))
      : form.injectEntries
  form.preRefreshSeconds =
    Number.isFinite(Number(config.refresh_policy?.pre_refresh_seconds))
      ? Number(config.refresh_policy.pre_refresh_seconds)
      : form.preRefreshSeconds
  form.retryOnceOn401 =
    typeof config.refresh_policy?.retry_once_on_401 === 'boolean'
      ? config.refresh_policy.retry_once_on_401
      : form.retryOnceOn401
  form.tokenUrl = tokenRequest.url || ''
  form.tokenMethod = tokenRequest.method || form.tokenMethod
  form.tokenBodyType = tokenRequest.body_type || form.tokenBodyType
  form.tokenHeaders = objectToKeyValueRows(tokenRequest.headers, form.tokenHeaders)
  form.tokenBodyTemplate = objectToKeyValueRows(tokenRequest.body_template, form.tokenBodyTemplate)
  form.tokenResponseMap = objectToKeyValueRows(tokenRequest.response_map, form.tokenResponseMap)

  return form
}

const normalizeInjectEntries = (entries) =>
  (Array.isArray(entries) ? entries : [])
    .map((entry) => ({
      name: normalizeText(entry?.name),
      value_template: String(entry?.value_template ?? '').trim()
    }))
    .filter((entry) => entry.name)

export const buildAuthConfigFromBuilderForm = (form) => {
  if (!form || form.provider === 'none') {
    return null
  }

  const provider = FORM_AUTH_PROVIDERS.has(form.provider) ? form.provider : 'custom_http_token'
  const config = {
    version: 1,
    provider,
    binding_scope: form.bindingScope || 'department',
    manifest_scope: form.manifestScope || 'binding',
    inject: {
      target: form.injectTarget || 'headers',
      entries: normalizeInjectEntries(form.injectEntries)
    },
    refresh_policy: {
      pre_refresh_seconds: Number(form.preRefreshSeconds) || 0,
      retry_once_on_401: Boolean(form.retryOnceOn401)
    }
  }

  if (TOKEN_PROVIDERS.has(provider)) {
    config.token_request = {
      url: normalizeText(form.tokenUrl),
      method: normalizeText(form.tokenMethod || 'POST').toUpperCase(),
      body_type: form.tokenBodyType || 'json',
      headers: keyValueRowsToObject(form.tokenHeaders),
      body_template: keyValueRowsToObject(form.tokenBodyTemplate),
      response_map: keyValueRowsToObject(form.tokenResponseMap)
    }
  }

  return config
}

export const extractSecretFieldNames = (value, fields = new Set()) => {
  if (typeof value === 'string') {
    const pattern = /\$\{secret\.([^}]+)\}/g
    let match = pattern.exec(value)
    while (match) {
      fields.add(match[1])
      match = pattern.exec(value)
    }
    return [...fields]
  }

  if (Array.isArray(value)) {
    value.forEach((item) => extractSecretFieldNames(item, fields))
    return [...fields]
  }

  if (value && typeof value === 'object') {
    Object.values(value).forEach((item) => extractSecretFieldNames(item, fields))
  }

  return [...fields]
}
