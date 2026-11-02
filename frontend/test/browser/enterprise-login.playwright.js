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
      await page.locator('.ant-form-item-explain-error').first().waitFor()
    } else {
      await page.getByRole('button', { name: '企业统一登录', exact: true }).waitFor()
      await page.getByText(error, { exact: true }).waitFor()
    }
    // 等待表单错误展开，按实际内容边界检查，避免只检查按钮是否挂载。
    await page.waitForFunction(() =>
      [...document.getAnimations()].every((animation) => animation.playState !== 'running')
    )
    const clipped = await page.evaluate(() => {
      const card = document.querySelector('.login-card').getBoundingClientRect()
      const form = document.querySelector('.form-wrapper').getBoundingClientRect()
      return form.top < card.top || form.bottom > card.bottom
    })
    if (clipped) throw new Error('登录表单及错误信息被登录卡片裁切')
    await page.screenshot({
      path: initializing ? '/tmp/yuxi-initialize-validation.png' : '/tmp/yuxi-login-error.png',
      fullPage: true
    })
  }
  return {
    passed: [
      'OIDC and password entries',
      'optional SSO hidden',
      'OIDC error remains inside card at 800×600',
      'initialization validation remains inside card at 800×600'
    ]
  }
}
