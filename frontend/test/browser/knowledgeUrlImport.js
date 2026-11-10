// 固定 HTTP 业务拒绝验证真实文件添加弹窗；抓取器与安全边界由 integration 验证。
async page => {
  const check = (value, reason) => { if (!value) throw new Error(reason) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const messages = [
    'URL 导入未启用，请联系管理员配置域名白名单',
    'URL 校验失败：网址域名不在 URL 导入白名单中，请联系管理员配置',
    '网页抓取失败，请确认网址可访问后重试'
  ]
  let current = 0
  await page.route('**/api/**', async route => {
    const path = '/' + route.request().url().split('/').slice(3).join('/').split('?')[0]
    if (path === '/api/knowledge/files/fetch-url') {
      check(route.request().method() === 'POST', 'URL 抓取请求方法不正确')
      check(route.request().postDataJSON().url === 'https://example.com/article', 'URL 抓取请求缺少原始网址')
      return route.fulfill({ status: 400, json: { detail: { code: current === 2 ? 'url_fetch_failed' : 'url_fetch_rejected', message: messages[current], context: 'private-url-diagnostic' } } })
    }
    return route.fulfill({ json: { file_types: ['.html', '.txt'] } })
  })
  try {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${origin}/test/browser/fixtures/knowledgeUrlImport.html`)
    const dialog = page.getByRole('dialog', { name: '添加文件' })
    const input = dialog.getByPlaceholder(/输入 URL/)
    for (current = 0; current < messages.length; current++) {
      if (current) await dialog.locator('.remove-url-btn').click()
      if (current === 1) {
        await page.evaluate(() => window.urlImportFixture.theme.setTheme('dark'))
        await page.setViewportSize({ width: 768, height: 900 })
      }
      await input.fill('https://example.com/article')
      await dialog.getByRole('button', { name: '加载 URLs', exact: true }).click()
      await dialog.locator('.url-error-msg').getByText(messages[current], { exact: true }).waitFor()
      check(await dialog.getByRole('button', { name: '添加到知识库', exact: true }).isDisabled(), '失败 URL 被允许入库')
      check(!(await dialog.innerText()).includes('private-url-diagnostic'), '内部诊断进入页面')
      check(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1), '错误文案使弹窗溢出')
      if (current < 2) await dialog.screenshot({ path: `/tmp/yuxi-url-import-${current === 0 ? 'disabled-light' : 'blocked-dark-768'}.png`, animations: 'disabled' })
    }
    return { errorCases: messages.length, screenshots: 2 }
  } finally {
    await page.unroute('**/api/**')
  }
}
