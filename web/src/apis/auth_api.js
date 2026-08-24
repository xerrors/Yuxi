/**
 * 认证相关 API
 */

import { apiDelete, apiGet, apiPost, apiPut } from './base'

async function parseErrorDetail(response, fallbackMessage) {
  const contentType = response.headers.get('content-type') || ''

  if (contentType.includes('application/json')) {
    const error = await response.json()
    return error?.detail || fallbackMessage
  }

  const text = (await response.text()).trim()
  return text || fallbackMessage
}

/** 使用一次性凭证交换 Yuxi 登录态。 */
async function exchangeLoginCredential(url, payload, fallbackMessage) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })

  if (!response.ok) {
    throw new Error(await parseErrorDetail(response, fallbackMessage))
  }
  return response.json()
}

/**
 * 获取 OIDC 配置
 * @returns {Promise<{enabled: boolean, provider_name?: string}>}
 */
async function getOIDCConfig() {
  return apiGet('/api/auth/oidc/config', {}, false)
}

/**
 * 获取 OIDC 登录 URL
 * @param {string} redirectPath - 登录后的重定向路径
 * @returns {Promise<{login_url: string}>}
 */
async function getOIDCLoginUrl(redirectPath = '/') {
  const params = new URLSearchParams({ redirect_path: redirectPath })
  return apiGet(`/api/auth/oidc/login-url?${params}`, {}, false)
}

/**
 * 使用一次性 code 交换 OIDC 登录结果
 * @param {string} code - 一次性登录 code
 * @returns {Promise<{
 *   access_token: string,
 *   token_type: string,
 *   user_id: number,
 *   username: string,
 *   uid: string,
 *   phone_number: string | null,
 *   avatar: string | null,
 *   roles: Array,
 *   effective_permissions: string[],
 *   department_id: number | null,
 *   department_name: string | null
 * }>}
 */
async function getUserAccessOptions() {
  return apiGet('/api/auth/users/access-options')
}

async function checkUidAvailability(uid) {
  return apiGet(`/api/auth/check-uid/${encodeURIComponent(uid)}`)
}

async function exchangeOIDCCode(code) {
  return exchangeLoginCredential('/api/auth/oidc/exchange-code', { code }, 'OIDC 登录失败')
}

/** 使用 OA 现有登录凭证交换 Yuxi token。 */
async function exchangeOAToken(token) {
  return exchangeLoginCredential('/api/auth/oa/exchange-token', { token }, 'OA 免登录失败')
}

/** 使用父项目提供的 OA 账号换取仅限内网试用的 Yuxi 登录态。 */
async function exchangeOAAccount(account) {
  return exchangeLoginCredential('/api/auth/oa/exchange-account', { account }, 'OA 账号登录失败')
}

async function login(credentials) {
  const formData = new FormData()
  formData.append('username', credentials.loginId)
  formData.append('password', credentials.password)
  return apiPost('/api/auth/token', formData, {}, false)
}

async function initialize(admin) {
  return apiPost('/api/auth/initialize', admin, {}, false)
}

async function checkFirstRun() {
  return apiGet('/api/auth/check-first-run', {}, false)
}

async function getUsers({ skip = 0, limit = 100 } = {}) {
  const params = new URLSearchParams({ skip: String(skip), limit: String(limit) })
  return apiGet(`/api/auth/users?${params}`)
}

async function createUser(userData) {
  return apiPost('/api/auth/users', userData)
}

async function updateUser(userId, userData) {
  return apiPut(`/api/auth/users/${encodeURIComponent(userId)}`, userData)
}

async function deleteUser(userId) {
  return apiDelete(`/api/auth/users/${encodeURIComponent(userId)}`)
}

async function validateUsername(username) {
  return apiPost('/api/auth/validate-username', { username })
}

async function uploadAvatar(file) {
  const formData = new FormData()
  formData.append('file', file)
  return apiPost('/api/auth/upload-avatar', formData)
}

async function getCurrentUser(signal) {
  return apiGet('/api/auth/me', signal ? { signal } : {})
}

async function updateProfile(profileData) {
  return apiPut('/api/auth/profile', profileData)
}

async function impersonateUser(userId) {
  return apiPost(`/api/auth/impersonate/${encodeURIComponent(userId)}`, {})
}

async function getCLIAuthSession(userCode) {
  const encoded = encodeURIComponent(userCode)
  return apiGet(`/api/auth/cli/sessions/${encoded}`)
}

async function approveCLIAuthSession(userCode) {
  const encoded = encodeURIComponent(userCode)
  return apiPost(`/api/auth/cli/sessions/${encoded}/approve`, {})
}

export const authApi = {
  login,
  initialize,
  checkFirstRun,
  getUsers,
  createUser,
  updateUser,
  deleteUser,
  validateUsername,
  uploadAvatar,
  getCurrentUser,
  updateProfile,
  impersonateUser,
  getOIDCConfig,
  getOIDCLoginUrl,
  getUserAccessOptions,
  checkUidAvailability,
  exchangeOIDCCode,
  exchangeOAToken,
  exchangeOAAccount,
  getCLIAuthSession,
  approveCLIAuthSession
}
