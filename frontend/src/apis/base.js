import {
  assertApiAdminPermission,
  assertApiSuperAdminPermission,
  getApiAuthHeaders,
  handleApiUnauthorized,
  getApiToken
} from './auth_context'
import { message } from 'ant-design-vue'

function safeRequestMetadata(url, requestOptions, response = null) {
  let path = '[invalid-url]'
  try {
    path = new URL(url, 'http://yuxi.local').pathname
  } catch {
    // 不把无法解析的原始 URL 写入日志，其中可能包含凭据或其他敏感查询参数。
  }

  return {
    path,
    method: requestOptions?.method || 'GET',
    ...(response
      ? {
          status: response.status
        }
      : {})
  }
}

const SAFE_ERROR_RESPONSE_HEADERS = ['x-lock-remaining', 'retry-after', 'www-authenticate']

/**
 * 统一构建查询参数，保留 0 和 false，省略空值。
 */
export const buildQuery = (params) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  })
  return query.toString()
}

function safeResponseHeaders(headers) {
  const safeHeaders = new Headers()
  for (const name of SAFE_ERROR_RESPONSE_HEADERS) {
    const value = headers.get(name)
    if (value !== null) safeHeaders.set(name, value)
  }
  return safeHeaders
}

function safeErrorData(errorData, status, publicMessage) {
  if (status !== 422) return { detail: publicMessage }
  const detail = errorData?.detail
  if (!Array.isArray(detail)) return { detail: publicMessage }
  return {
    detail: detail.map((item) => ({
      loc: Array.isArray(item?.loc) ? item.loc.map((part) => String(part)) : [],
      msg: '请求参数验证失败',
      type: typeof item?.type === 'string' ? item.type : 'validation_error'
    }))
  }
}

/**
 * 提取后端业务错误的可展示 detail。对象形态仅透传 code 与 message 两个字符串字段
 * （其余字段一律丢弃）。
 *
 * 字符串 detail 只在状态码本身表达"请求与当前资源状态冲突"时透传（409/410），
 * 例如 {"detail":"Thread 已归档"}；这类文案说明的是用户可自行处理的状态，且比
 * "请刷新后重试" 更准确。401/403/422/5xx 仍使用脱敏兜底文案，避免把权限或
 * 服务端内部信息原样展示给终端用户。
 * 数组形态（FastAPI schema 校验，可能回显敏感输入）不适用此契约。
 */
const STRING_DETAIL_STATUSES = new Set([409, 410])

function extractBusinessErrorDetail(errorData, status) {
  const detail = errorData?.detail
  if (typeof detail === 'string') {
    if (!STRING_DETAIL_STATUSES.has(status)) return null
    const message = detail.trim()
    return message ? { code: '', message } : null
  }
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null
  if (typeof detail.code !== 'string' || typeof detail.message !== 'string') return null
  return { code: detail.code, message: detail.message }
}

/** 从请求错误中读取业务文案，保留公共请求层已脱敏的错误信息。 */
export function resolveApiErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message.trim()) {
    return detail.message
  }
  if (typeof detail === 'string' && detail.trim()) return detail
  if (typeof error?.message === 'string' && error.message.trim()) return error.message
  return fallback
}

function publicErrorMessage(url, status, headers, requiresAuth) {
  const path = safeRequestMetadata(url, {}).path
  if (status === 400) return '请求参数错误'
  if (status === 401) {
    if (requiresAuth) return '登录已过期，请重新登录'
    return path === '/api/auth/token' ? '用户名或密码错误' : '认证请求失败'
  }
  if (status === 403) {
    return !requiresAuth && path === '/api/auth/token' ? '账号已注销，请联系管理员' : '没有权限执行此操作'
  }
  if (status === 404) return '请求资源不存在'
  if (status === 409) return '请求冲突，请刷新后重试'
  if (status === 410) return '请求已失效'
  if (status === 413) return '请求内容过大'
  if (status === 422) return '请求参数验证失败'
  if (status === 423) {
    const remaining = Number.parseInt(headers.get('x-lock-remaining') || '', 10)
    return Number.isSafeInteger(remaining) && remaining > 0
      ? `账户已锁定 ${remaining} 秒`
      : '账户已锁定，请稍后再试'
  }
  if (status === 429) return '请求过于频繁，请稍后重试'
  if (status >= 500) return '服务器内部错误，请稍后重试'
  return `请求失败: ${status}`
}

/**
 * 基础API请求封装
 * 提供统一的请求方法，自动处理认证头和错误
 */

/**
 * 发送API请求的基础函数
 * @param {string} url - API端点
 * @param {Object} options - 请求选项
 * @param {boolean} requiresAuth - 是否需要认证头
 * @param {string} responseType - 响应类型: 'json' | 'text' | 'blob'
 * @returns {Promise} - 请求结果
 */
export async function apiRequest(url, options = {}, requiresAuth = true, responseType = 'json') {
  try {
    const isFormData = options?.body instanceof FormData
    // 默认请求配置
    const requestOptions = {
      ...options,
      headers: {
        ...(!isFormData ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers
      }
    }

    // 如果需要认证，添加认证头
    if (requiresAuth) {
      if (!getApiToken()) {
        throw new Error('用户未登录')
      }

      Object.assign(requestOptions.headers, getApiAuthHeaders())
    }

    // 发送请求
    const response = await fetch(url, requestOptions)

    // 处理API返回的错误
    if (!response.ok) {
      // 尝试解析错误信息
      const errorMessage = publicErrorMessage(url, response.status, response.headers, requiresAuth)
      let errorData = null

      console.error('API请求失败:', safeRequestMetadata(url, requestOptions, response))

      try {
        errorData = await response.json()
        // 422 常由请求校验失败引起。只记录不可逆的请求元数据；认证头、请求体与
        // 服务端响应都可能包含密码、令牌或其他隐私数据，禁止写入浏览器日志。
        if (response.status === 422) {
          console.error('API请求校验失败:', safeRequestMetadata(url, requestOptions, response))
        }
      } catch {
        // 如果无法解析JSON，使用默认错误信息
        console.error('API错误响应无法解析:', safeRequestMetadata(url, requestOptions, response))
      }

      // 特殊处理401和403错误
      const businessDetail = extractBusinessErrorDetail(errorData, response.status)
      const error = new Error(businessDetail?.message || errorMessage)
      error.status = response.status
      error.headers = safeResponseHeaders(response.headers)
      error.response = {
        status: response.status,
        data: businessDetail
          ? { detail: businessDetail }
          : safeErrorData(errorData, response.status, errorMessage),
        headers: error.headers
      }

      if (response.status === 401 && requiresAuth) {
        // 如果是认证失败，可能需要重新登录
        message.error('登录已过期，请重新登录')

        // 将会话清理交还给 identity Store
        handleApiUnauthorized()

        // 使用setTimeout确保消息显示后再跳转
        setTimeout(() => {
          window.location.href = '/login'
        }, 1500)

        throw error
      } else if (response.status === 403) {
        throw error
      } else if (response.status === 500) {
        throw error
      }

      throw error
    }

    // 根据responseType处理响应
    if (responseType === 'blob') {
      return response
    } else if (responseType === 'json') {
      // 检查Content-Type以确定如何处理响应
      const contentType = response.headers.get('Content-Type')
      if (contentType && contentType.includes('application/json')) {
        return await response.json()
      }
      return await response.text()
    } else if (responseType === 'text') {
      return await response.text()
    } else {
      return response
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('API请求异常:', {
        ...safeRequestMetadata(url, options),
        status: error?.status ?? null,
        errorType: error?.name || 'Error'
      })
    }
    throw error
  }
}

/**
 * 发送GET请求
 * @param {string} url - API端点
 * @param {Object} options - 请求选项
 * @param {boolean} requiresAuth - 是否需要认证
 * @param {string} responseType - 响应类型: 'json' | 'text' | 'blob'
 * @returns {Promise} - 请求结果
 */
export function apiGet(url, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(url, { method: 'GET', ...options }, requiresAuth, responseType)
}

export function apiAdminGet(url, options = {}, responseType = 'json') {
  assertApiAdminPermission()
  return apiGet(url, options, true, responseType)
}

export function apiSuperAdminGet(url, options = {}, responseType = 'json') {
  assertApiSuperAdminPermission()
  return apiGet(url, options, true, responseType)
}

/**
 * 发送POST请求
 * @param {string} url - API端点
 * @param {Object} data - 请求体数据
 * @param {Object} options - 其他请求选项
 * @param {boolean} requiresAuth - 是否需要认证
 * @param {string} responseType - 响应类型: 'json' | 'text' | 'blob'
 * @returns {Promise} - 请求结果
 */
export function apiPost(url, data = {}, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(
    url,
    {
      method: 'POST',
      body: data instanceof FormData ? data : JSON.stringify(data),
      ...options
    },
    requiresAuth,
    responseType
  )
}

export function apiAdminPost(url, data = {}, options = {}, responseType = 'json') {
  assertApiAdminPermission()
  return apiPost(url, data, options, true, responseType)
}

export function apiSuperAdminPost(url, data = {}, options = {}, responseType = 'json') {
  assertApiSuperAdminPermission()
  return apiPost(url, data, options, true, responseType)
}

/**
 * 发送PUT请求
 * @param {string} url - API端点
 * @param {Object} data - 请求体数据
 * @param {Object} options - 其他请求选项
 * @param {boolean} requiresAuth - 是否需要认证
 * @param {string} responseType - 响应类型: 'json' | 'text' | 'blob'
 * @returns {Promise} - 请求结果
 */
export function apiPut(url, data = {}, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(
    url,
    {
      method: 'PUT',
      body: data instanceof FormData ? data : JSON.stringify(data),
      ...options
    },
    requiresAuth,
    responseType
  )
}

export function apiAdminPut(url, data = {}, options = {}, responseType = 'json') {
  assertApiAdminPermission()
  return apiPut(url, data, options, true, responseType)
}

export function apiSuperAdminPut(url, data = {}, options = {}, responseType = 'json') {
  assertApiSuperAdminPermission()
  return apiPut(url, data, options, true, responseType)
}

/**
 * 发送DELETE请求
 * @param {string} url - API端点
 * @param {Object} options - 请求选项
 * @param {boolean} requiresAuth - 是否需要认证
 * @param {string} responseType - 响应类型: 'json' | 'text' | 'blob'
 * @returns {Promise} - 请求结果
 */
export function apiDelete(url, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(url, { method: 'DELETE', ...options }, requiresAuth, responseType)
}

export function apiAdminDelete(url, options = {}) {
  assertApiAdminPermission()
  return apiDelete(url, options, true)
}

export function apiSuperAdminDelete(url, options = {}) {
  assertApiSuperAdminPermission()
  return apiDelete(url, options, true)
}
