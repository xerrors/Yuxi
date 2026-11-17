// 复制当前登录态到隔离浏览器上下文；凭据保存只命中合成 HTTP 边界。
// playwright-cli -s=ui-fixes run-code --filename=frontend/test/browser/uiRecoveryBoundaries.js
async (page) => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const browser = page.context().browser()
  const storageState = await page.context().storageState()
  const check = (value, message) => { if (!value) throw new Error(message) }
  const results = []
  for (const failure of ['502', 'network', '401']) {
    const context = await browser.newContext({ storageState, viewport: { width: 1024, height: 800 } })
    try {
      const target = await context.newPage()
      await target.route('**/api/auth/me', (route) => failure === 'network'
        ? route.abort('failed')
        : route.fulfill({ status: Number(failure), json: { detail: '测试服务错误' } }))
      await target.goto(`${origin}/extensions?tab=knowledge`)
      if (failure === '401') {
        await target.waitForURL('**/login')
        check(!await target.evaluate(() => !!localStorage.getItem('user_token')), '401 后仍保留失效凭据')
      } else {
        await target.locator('.route-error').waitFor()
        check(await target.evaluate(() => !!localStorage.getItem('user_token')), '临时失败清除了凭据')
        check(await target.locator('#app-router-view').count() === 0, '身份未验证却挂载业务页面')
        await target.screenshot({ path: `/tmp/yuxi-ui-fixes-evidence/identity-${failure}.png` })
        await target.unroute('**/api/auth/me')
        await target.getByRole('button', { name: '重新加载', exact: true }).click()
        await target.locator('.knowledge-base-container').waitFor()
        check(target.url().includes('/extensions'), '恢复后没有打开原目标')
        check(await target.locator('.route-error').count() === 0, '恢复后残留错误面板')
      }
      results.push(`Identity ${failure}: expected credential and navigation outcome`)
    } finally { await context.close() }
  }

  const context = await browser.newContext({ storageState, viewport: { width: 1440, height: 900 } })
  try {
    const target = await context.newPage()
    let saved = 'fixture-existing'
    const writes = []
    const option = () => ({
      key: 'tavily_search_opts', name: 'Tavily', value: { api_key: '' },
      params: { provider: { id: 'tavily', supports_fetch: true, environment: 'TAVILY_API_KEY' } },
      sensitive_state: { api_key: { configured: !!saved, source: saved ? 'database' : 'unset', preview: 'fixture-****' } }
    })
    await target.route('**/api/system/config/options', (route) => route.fulfill({ json: { options: [option()] } }))
    await target.route('**/api/system/config/options/tavily_search_opts', async (route) => {
      check(route.request().method() === 'PUT', '凭据提交方法不正确')
      const payload = route.request().postDataJSON()
      writes.push(payload)
      saved = payload.value.api_key
      await route.fulfill({ json: { option: option() } })
    })
    await target.goto(`${origin}/agent-manage`)
    await target.locator('.user-info-dropdown').click()
    await target.getByText('设置', { exact: true }).click()
    await target.locator('.settings-sider-nav').getByRole('button', { name: '网络搜索', exact: true }).click()
    const panel = target.locator('.web-settings')
    await panel.locator('.settings-service-card').hover()
    await panel.getByRole('button', { name: /编\s*辑/ }).click()
    await panel.getByRole('button', { name: /保\s*存/ }).click()
    await panel.getByRole('button', { name: /编\s*辑/ }).waitFor()
    check(saved === 'fixture-existing' && writes.length === 0, '空白保存修改了凭据')
    await panel.getByRole('button', { name: /编\s*辑/ }).click()
    await panel.getByRole('checkbox', { name: '清除已保存的 API Key', exact: true }).check()
    check(await panel.locator('input[type="password"]').isDisabled(), '清除与替换输入同时可用')
    await panel.screenshot({ path: '/tmp/yuxi-ui-fixes-evidence/credential-explicit-clear.png' })
    await panel.getByRole('button', { name: /保\s*存/ }).click()
    await panel.getByRole('button', { name: /编\s*辑/ }).waitFor()
    check(saved === '' && writes.length === 1, '显式清除没有提交空值')
    await panel.getByRole('button', { name: /编\s*辑/ }).click()
    check(!await panel.getByRole('checkbox').isChecked(), '重新编辑残留清除状态')
    await panel.locator('input[type="password"]').fill('fixture-replacement')
    await panel.getByRole('button', { name: /保\s*存/ }).click()
    await panel.getByRole('button', { name: /编\s*辑/ }).waitFor()
    check(saved === 'fixture-replacement' && writes.length === 2, '替换没有提交新值')
    results.push('Credential UI: blank preserves; explicit clear; replace; synthetic requests only')
  } finally { await context.close() }
  return results
}
