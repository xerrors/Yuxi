/** 解析并校验 OA iframe 的精确 HTTP origin 白名单。 */
export function parseOAEmbedAllowedOrigins(value) {
  return [
    ...new Set(
      String(value || '')
        .split(/[\s,]+/)
        .filter(Boolean)
        .filter((candidate) => {
          try {
            const url = new URL(candidate)
            return ['http:', 'https:'].includes(url.protocol) && url.origin === candidate
          } catch {
            return false
          }
        })
    )
  ]
}

export const OA_EMBED_MODES = Object.freeze(['fixed', 'floating', 'fullscreen'])
export const DEFAULT_OA_EMBED_MODE = 'fixed'

/** 计算嵌入登录态的静默续期等待时间，最迟在过期前一小时请求父页续期。 */
export function getOAEmbedRenewalDelay(accessToken, now = Date.now()) {
  try {
    const payload = accessToken.split('.')[1]
    const encoded = payload.replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(encoded))
    const expiresAt = Number(claims.exp) * 1000
    if (!Number.isFinite(expiresAt)) return null
    return Math.max(0, expiresAt - now - 60 * 60 * 1000)
  } catch {
    return null
  }
}

/**
 * 创建 OA iframe 的 postMessage 桥，登录握手遵循正式 OA 父插件协议。
 */
export function createOAEmbedBridge({
  allowedOrigins,
  onAccount,
  onModeChanged = () => {},
  browserWindow = window,
  setTimer = setTimeout,
  clearTimer = clearTimeout
}) {
  const origins = allowedOrigins
  let parentOrigin = ''
  let loginRequestTimer = null
  let tokenAccepted = false
  let isAuthenticating = false
  let baseMode = DEFAULT_OA_EMBED_MODE
  let currentMode = DEFAULT_OA_EMBED_MODE

  /** 向已确认或候选的 OA 父页面发送正式协议消息。 */
  const post = (message) => {
    const targets = parentOrigin ? [parentOrigin] : origins
    targets.forEach((targetOrigin) => {
      browserWindow.parent.postMessage(message, targetOrigin)
    })
  }

  /** 请求父项目重新下发当前 OA 账号，用于首次登录和 Yuxi 登录态续期。 */
  const requestLoginParams = () => {
    console.info('[OA iframe] 请求父项目登录参数')
    post({ type: 'request-login-params', data: { timestamp: Date.now() } })
  }

  /** 将正式父插件的初始显示模式转换为 Yuxi 嵌入显示模式。 */
  const applyInitialMode = (mode) => {
    const mappedMode = mode === 'embedded' ? 'fixed' : mode
    if (!['fixed', 'floating'].includes(mappedMode)) {
      console.warn('[OA iframe] 忽略不支持的初始显示模式')
      return
    }

    baseMode = mappedMode
    currentMode = mappedMode
    console.info('[OA iframe] 已同步父项目初始显示模式')
    onModeChanged(mappedMode)
  }

  const handleMessage = async (event) => {
    if (event.source !== browserWindow.parent || !origins.includes(event.origin)) return

    const messageType = event.data?.type
    if (messageType === 'initial-mode') {
      parentOrigin = event.origin
      applyInitialMode(event.data.mode)
      return
    }
    if (messageType === 'host-info' || messageType === 'init-data' || messageType === 'helper-type') {
      // 当前 Yuxi 没有与旧 H5 相同的助手类型或宿主信息消费场景，安全接收后不参与业务处理。
      parentOrigin = event.origin
      console.info('[OA iframe] 收到父项目初始化消息，当前无需处理')
      return
    }
    if (messageType !== 'login-params') return

    const account = event.data?.data?.userInfo?.account
    if (typeof account !== 'string' || !account.trim() || typeof onAccount !== 'function') {
      console.warn('[OA iframe] 忽略缺少有效账号的登录参数')
      return
    }
    if (isAuthenticating) {
      console.warn('[OA iframe] 正在验证账号，忽略重复登录参数')
      return
    }

    parentOrigin = event.origin
    isAuthenticating = true
    console.info('[OA iframe] 收到父项目账号，开始建立 Yuxi 登录态')
    try {
      await onAccount(account.trim())
      tokenAccepted = true
    } catch {
      console.warn('[OA iframe] OA 账号登录失败，等待父项目重新授权')
    } finally {
      isAuthenticating = false
    }

  }

  return {
    start() {
      browserWindow.addEventListener('message', handleMessage)
      console.info('[OA iframe] 桥接监听已启动，通知父项目应用就绪')
      post({ type: 'ready' })

      // 正式 H5 在 ready 后延迟请求账号，避免与父插件初始化消息竞争。
      loginRequestTimer = setTimer(() => {
        if (!tokenAccepted) requestLoginParams()
      }, 500)
    },
    stop() {
      browserWindow.removeEventListener('message', handleMessage)
      if (loginRequestTimer !== null) clearTimer(loginRequestTimer)
      loginRequestTimer = null
      isAuthenticating = false
    },
    requestAuthRequired() {
      tokenAccepted = false
      requestLoginParams()
    },
    requestMode(mode) {
      if (!OA_EMBED_MODES.includes(mode)) return false
      if (mode === currentMode) return false

      if (mode === 'fullscreen') {
        post({ type: 'toggle-window-size', data: { isEnlarge: true } })
        currentMode = 'fullscreen'
        console.info('[OA iframe] 已请求父项目进入全屏模式')
        return true
      }

      if (currentMode === 'fullscreen') {
        post({ type: 'toggle-window-size', data: { isEnlarge: false } })
      }
      if (mode !== baseMode) {
        post({ type: 'toggle-floating', data: { isFloating: mode === 'floating' } })
        baseMode = mode
      }

      currentMode = mode
      console.info('[OA iframe] 已请求父项目切换显示模式')
      return true
    },
    requestClose(threadId) {
      console.info('[OA iframe] 已请求父项目关闭助手')
      post({ type: 'close', data: threadId ? { threadId } : {} })
    }
  }
}
