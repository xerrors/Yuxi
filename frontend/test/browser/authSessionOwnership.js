// 独立 Vite 会话中延迟旧凭据的真实浏览器请求。
// prettier-ignore
async page => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const oldToken = 'frontend-audit-previous'
  const newToken = 'frontend-audit-current'
  const { promise: staleRequested, resolve: notifyStaleRequested } = Promise.withResolvers()
  const { promise: staleRelease, resolve: releaseStale } = Promise.withResolvers()
  const { promise: logoutRequested, resolve: notifyLogoutRequested } = Promise.withResolvers()
  const { promise: logoutRelease, resolve: releaseLogout } = Promise.withResolvers()
  let logoutCalls = 0
  await page.addInitScript(token => {
    if (sessionStorage.getItem('auditInitialized')) return
    localStorage.setItem('user_token', token)
    sessionStorage.setItem('auditInitialized', '1')
  }, oldToken)
  await page.route('https://api.github.com/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('?')[0].replace(origin, '')
    const previous = route.request().headers().authorization === 'Bearer ' + oldToken
    switch (path) {
      case '/api/auth/oidc/logout':
        if (++logoutCalls === 1) {
          notifyLogoutRequested()
          await logoutRelease
        }
        return route.fulfill({ json: {} })
      case '/api/auth/me':
        return route.fulfill({ json: { id: 1, uid: 'previous', username: '旧会话', role: 'user' } })
      case '/api/auth/token':
        return route.fulfill({ json: { access_token: newToken, user_id: 2, uid: 'current', username: '新会话', role: 'user' } })
      case '/api/auth/check-first-run':
        return route.fulfill({ json: { first_run: false } })
      case '/api/auth/oidc/config':
        return route.fulfill({ json: { enabled: false } })
      case '/api/system/config':
        if (previous) {
          notifyStaleRequested()
          await staleRelease
          return route.fulfill({ status: 401, json: { detail: '旧凭据失效' } })
        }
        return route.fulfill({ json: { config: {} } })
      case '/api/system/info':
        return route.fulfill({ json: { data: { branding: { name: 'Yuxi' } } } })
      case '/api/system/discovery':
        return route.fulfill({ json: { capabilities: { features: { knowledge: false } } } })
      case '/api/agent':
        return route.fulfill({ json: { agents: [] } })
      case '/api/projects':
        return route.fulfill({ json: [] })
      default:
        return route.fulfill({ json: { data: [], files: [], projects: [], has_more: false } })
    }
  })
  try {
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.goto(origin + '/agent')
    await staleRequested
    await page.locator('.user-info-dropdown').click()
    await page.getByRole('menuitem', { name: '退出登录' }).click()
    await logoutRequested
    await page.locator('.user-info-dropdown').click()
    await page.getByRole('menuitem', { name: '退出登录' }).click()
    await page.waitForURL('**/login')
    await page.getByPlaceholder('UID或手机号').fill('current')
    await page.locator('input[type=password]').fill('synthetic password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.waitForURL('**/agent')
    await page.getByText('新会话', { exact: true }).waitFor()
    const logoutResponse = page.waitForResponse(response => response.url().endsWith('/api/auth/oidc/logout'))
    releaseLogout()
    await (await logoutResponse).finished()
    const staleResponse = page.waitForResponse(response => response.url().endsWith('/api/system/config') && response.status() === 401)
    releaseStale()
    await (await staleResponse).finished()
    // 观察窗口覆盖现有的过期跳转延时。
    await page.waitForTimeout(1700)
    const currentSession = await page.evaluate(() => localStorage.getItem('user_token'))
    if (currentSession !== newToken || !page.url().endsWith('/agent')) throw new Error('旧响应退出了新会话')
    await page.getByText('新会话', { exact: true }).waitFor()
    return { oldResponseStatus: 401, activeSessionRetained: true, currentPage: '/agent' }
  } finally {
    releaseLogout()
    releaseStale()
    await page.unrouteAll({ behavior: 'wait' })
  }
}
