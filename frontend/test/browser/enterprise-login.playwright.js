// 使用 playwright-cli run-code --filename 调用；fixture 只验证前端，真实 OIDC 另行联调。
// eslint-disable-next-line no-unused-vars -- playwright-cli 调用函数表达式。
async function verifyEnterpriseLoginAndWorkspace(page) {
  const origin = await page.evaluate(() => location.origin)
  let oidcEnabled = true
  let firstRun = false
  await page.addInitScript(() => localStorage.removeItem('user_token'))
  await page.route('**/api/**', async (route) => {
    const url = route.request().url()
    const respond = (body) =>
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (url.includes('/system/health')) return respond({ status: 'ok' })
    if (url.includes('/check-first-run')) return respond({ first_run: firstRun })
    if (url.includes('/oidc/'))
      return respond({ enabled: oidcEnabled, provider_name: '企业统一登录' })
    if (url.endsWith('/system/info'))
      return respond({
        success: true,
        data: {
          branding: { name: 'Yuxi', logo: '/favicon.svg' },
          footer: {
            user_agreement_url: 'https://example.com/agreement',
            privacy_policy_url: 'https://example.com/privacy'
          }
        }
      })
    if (url.endsWith('/auth/me'))
      return respond({
        id: 1,
        uid: 'fixture',
        username: 'Fixture user',
        role: 'user',
        department_id: 1
      })
    if (url.includes('/api/v1/agents/sessions')) return respond({ data: [], has_more: false })
    if (url.includes('/workspace/tree')) return respond({ entries: [], path: '/' })
    if (url.includes('/knowledge')) return respond({ knowledge_bases: [] })
    return respond([])
  })
  await page.goto(origin + '/login')
  await page.getByRole('button', { name: '企业统一登录', exact: true }).waitFor()
  await page.locator('input[type="password"]').waitFor()
  await page.screenshot({ path: '/tmp/yuxi-enterprise-login.png', fullPage: true })
  oidcEnabled = false
  await page.reload()
  await page.locator('input[type="password"]').waitFor()
  await page.waitForLoadState('networkidle')
  if (await page.locator('.third-party-login').count())
    throw new Error('无 OIDC 时仍显示第三方区域')

  oidcEnabled = true
  await page.setViewportSize({ width: 800, height: 600 })
  for (const initializing of [false, true]) {
    firstRun = initializing
    const error = '身份认证服务暂时无法完成登录，请联系管理员确认账号和单点登录配置，检查身份提供方的连接状态后重试。'
    await page.goto(origin + '/login' + (initializing ? '' : '?oidc_error=' + encodeURIComponent(error)))
    if (initializing) {
      await page.getByLabel('UID', { exact: true }).fill('a')
      await page.getByLabel('手机号（可选）', { exact: true }).fill('123')
      await page.getByLabel('密码', { exact: true }).fill('example-password')
      await page.getByLabel('确认密码', { exact: true }).fill('different')
      await page.getByRole('button', { name: '创建管理员账户' }).click()
      await page.locator('.ant-message-error').getByText('UID长度必须在3-20个字符之间').waitFor()
    } else {
      await page.locator('.ant-message-error').getByText(error, { exact: true }).waitFor()
      await page.getByRole('button', { name: '企业统一登录', exact: true }).waitFor()
    }
    if (await page.locator('.error-message').count())
      throw new Error('登录错误仍渲染在卡片内')
    await page.waitForFunction(() =>
      [...document.getAnimations()].every((animation) => animation.playState !== 'running')
    )
    const layout = await page.evaluate(() => {
      const card = document.querySelector('.login-card').getBoundingClientRect()
      const form = document.querySelector('.form-wrapper').getBoundingClientRect()
      return {
        width: Math.round(card.width),
        height: Math.round(card.height),
        clipped: form.top < card.top - 1 || form.bottom > card.bottom + 1
      }
    })
    if (layout.width !== 900 || layout.height !== 560)
      throw new Error(`登录卡片尺寸不是 900×560：${layout.width}×${layout.height}`)
    if (layout.clipped) throw new Error('登录表单被固定尺寸的登录卡片裁切')
    await page.screenshot({
      path: initializing ? '/tmp/yuxi-initialize-validation.png' : '/tmp/yuxi-login-error.png',
      fullPage: true
    })
  }
  return {
    passed: [
      'OIDC and password entries',
      'optional SSO hidden',
      'OIDC error uses message at fixed 900×560 card',
      'initialization validation uses message at fixed 900×560 card'
    ]
  }
}
