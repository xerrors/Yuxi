let apiAuthContext = {
  getToken: readStoredToken,
  isAdmin: () => false,
  isSuperAdmin: () => false,
  onUnauthorized: clearStoredToken
}

function readStoredToken() {
  if (typeof globalThis === 'undefined' || !globalThis.localStorage) return ''
  return globalThis.localStorage.getItem('user_token') || ''
}

function clearStoredToken() {
  if (typeof globalThis !== 'undefined' && globalThis.localStorage) {
    globalThis.localStorage.removeItem('user_token')
  }
}

/** 注入应用拥有的认证状态，避免 API 层反向依赖 identity。 */
export function configureApiAuth(context = {}) {
  apiAuthContext = { ...apiAuthContext, ...context }
}

/** 返回当前会话 token；持久化事实仍由 identity Store 拥有。 */
export function getApiToken() {
  return apiAuthContext.getToken() || ''
}

/** 为普通 HTTP 与 Thread SSE 生成一致的认证头。 */
export function getApiAuthHeaders(token = getApiToken()) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** 执行 API 层的管理员体验守卫，最终授权由后端执行。 */
export function assertApiAdminPermission() {
  if (!apiAuthContext.isAdmin()) {
    throw new Error('需要管理员权限')
  }
  return true
}

/** 执行 API 层的超级管理员体验守卫，最终授权由后端执行。 */
export function assertApiSuperAdminPermission() {
  if (!apiAuthContext.isSuperAdmin()) {
    throw new Error('需要超级管理员权限')
  }
  return true
}

/** 将认证失效交还给 identity Store，并保留未装配时的安全降级。 */
export function handleApiUnauthorized() {
  apiAuthContext.onUnauthorized()
}
