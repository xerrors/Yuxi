const AUTO_START_KEY = 'oidc_auto_start_attempted'

// 对 redirect 参数进行安全校验，防止开放重定向漏洞
// - 必须是字符串类型
// - 必须以 / 开头（本地相对路径）
// - 不能以 // 或 \ 开头（协议相对 URL / 非标准路径）
// 不合法或缺失时返回 "/"
export function sanitizeRedirect(value) {
  if (typeof value === 'string' && value.length > 0 && value[0] === '/' && value[1] !== '/' && value[1] !== '\\') {
    return value
  }
  return '/'
}

// 检查是否已经尝试过自动触发 OIDC（同一会话内，避免无限循环）
export function hasAutoStartAttempted() {
  return sessionStorage.getItem(AUTO_START_KEY) === '1'
}

// 标记已尝试自动触发
export function markAutoStartAttempted() {
  sessionStorage.setItem(AUTO_START_KEY, '1')
}

// 清除自动触发尝试标记（OIDC 登录成功后调用）
export function clearAutoStartAttempt() {
  sessionStorage.removeItem(AUTO_START_KEY)
}

// 尝试自动触发 OIDC 登录
// config: OIDC 配置对象（由调用方在外部获取）
// getOIDCLoginUrl: 获取登录 URL 的异步函数
// 返回 true 表示已发起跳转，caller 不应继续执行后续流程
export async function tryAutoStartOIDC(getOIDCLoginUrl, config) {
  // 1. OIDC 配置未就绪或未启用
  if (!config || !config.enabled) {
    return false
  }

  // 2. 有 oidc_error 时不再自动触发，避免循环
  const params = new URLSearchParams(window.location.search)
  if (params.has('oidc_error')) {
    return false
  }

  // 3. 必须有 autostartOidc 参数
  if (!params.has('autostartOidc')) {
    return false
  }

  // 4. 同一会话内已尝试过，不再重复
  if (hasAutoStartAttempted()) {
    return false
  }

  // 5. 获取 OIDC 登录 URL 并跳转
  let loginUrlResp
  try {
    loginUrlResp = await getOIDCLoginUrl()
  } catch {
    return false
  }

  if (!loginUrlResp || !loginUrlResp.login_url) {
    return false
  }

  // 保存当前路径，登录后返回（安全校验）
  const redirectPath = sanitizeRedirect(params.get('redirect'))
  sessionStorage.setItem('oidc_redirect', redirectPath)

  // 标记已尝试，防止下次再自动触发
  markAutoStartAttempted()

  window.location.href = loginUrlResp.login_url
  return true
}
