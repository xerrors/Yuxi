// 在独立会话中运行：playwright-cli -s=runtime-settings run-code --filename=frontend/test/browser/runtimeSettings.js
// 使用真实设置弹窗与组件；所有 API 都由浏览器内的合成响应提供，不触碰已有配置。
// prettier-ignore
async page => {
  page.setDefaultTimeout(15000)
  const origin = page.url().split('/').slice(0, 3).join('/')
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const savedTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const originalViewport = page.viewportSize()
  const unknownRequests = []
  let env = { SILICONFLOW_API_KEY: 'synthetic-secret' }
  let revision = 1
  let mismatch = false
  let unavailable = false
  let releaseEnv
  let holdEnv = false
  let diskReads = 0
  let status = { backend: 'cube', state: 'paused', generation: 1, busy: false, active_runs: [], active_operations: 0,
    server_time: new Date().toISOString(), paused_at: new Date(Date.now() - 53000).toISOString(), idle_timeout_seconds: 300 }
  /** 只在隔离浏览器中回放配置与环境协议。 */
  const handler = async route => {
    const request = route.request()
    const path = '/' + request.url().split('/').slice(3).join('/').split('?')[0]
    const respond = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/user/agent-env') {
      if (holdEnv) await new Promise(resolve => { releaseEnv = resolve })
      if (request.method() === 'PUT') { env = request.postDataJSON().env; revision++ }
      return respond({ env, updated_at: `v${revision}` })
    }
    if (path === '/api/user/agent-env/verify') {
      status = { ...status, state: 'running' }
      return respond({ updated_at: `v${revision}`, verified_at: new Date().toISOString(), variables: Object.keys(env).map((key, index) => ({ key, matches: !mismatch || index !== 0 })) })
    }
    if (path === '/api/user/sandbox') return respond(unavailable ? { detail: { code: 'fixture_unavailable', message: '执行节点暂不可用' } } : status, unavailable ? 503 : 200)
    if (path === '/api/user/sandbox/disk-usage') {
      diskReads++
      status = { ...status, state: 'running' }
      return respond({ used_bytes: 269987676, available_bytes: 1577058304 })
    }
    if (path === '/api/system/config') return respond({})
    if (path === '/api/system/config/options') return respond({ options: [] })
    if (path === '/api/user/config') return respond({ enable_memory: false })
    if (path === '/api/user/message-activity') return respond({ daily_message_counts: [] })
    if (path === '/api/projects') return respond([])
    unknownRequests.push(`${request.method()} ${path}`)
    return respond({ detail: { code: 'fixture_unknown_request', message: '未声明的测试请求' } }, 501)
  }
  await page.route('**/api/**', handler)
  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${origin}/test/browser/settingsStyles.html`)
    await page.waitForFunction(() => Boolean(window.settingsStyles))
    await page.evaluate(() => window.settingsStyles.mount('modal', 'agentEnv'))
    const panel = page.locator('.agent-env-settings:visible')
    await panel.locator('.env-key').filter({ hasText: 'SILICONFLOW_API_KEY' }).waitFor()
    const more = panel.getByRole('button', { name: '环境操作', exact: true })
    const checkMenu = async () => {
      await more.click()
      const menu = page.locator('.action-dropdown-panel:visible')
      await menu.waitFor()
      await page.waitForFunction(() => {
        const menu = document.querySelector('.ant-dropdown:not(.ant-dropdown-hidden) .action-dropdown-panel')
        const trigger = document.querySelector('.agent-env-settings [aria-label="环境操作"]')
        if (!menu || !trigger) return false
        const panel = menu.getBoundingClientRect(), button = trigger.getBoundingClientRect()
        return Math.abs(panel.right - button.right) <= 1
      })
      const menuBox = await menu.boundingBox(), triggerBox = await more.boundingBox()
      check(Math.abs(menuBox.x + menuBox.width - triggerBox.x - triggerBox.width) <= 1, `dropdown 右边缘应与触发按钮对齐：${JSON.stringify({menuBox,triggerBox})}`)
      check(menuBox.x >= 12 && menuBox.x + menuBox.width <= page.viewportSize().width - 12, 'dropdown 不能越出视口')
      await page.getByRole('menuitem', { name: '重建环境' }).press('Escape')
      await menu.waitFor({ state: 'hidden' })
      check(await more.evaluate(el => el === document.activeElement), 'Escape 应返回触发按钮')
    }
    /** 读取真实 DOM 几何，确保变量仍在同一张横向卡片内。 */
    const checkCards = async width => {
      const geometry = await panel.locator('.env-card').evaluateAll(cards => cards.map(el => {
        const box = el.getBoundingClientRect()
        const key = el.querySelector('.env-key-cell').getBoundingClientRect()
        const value = el.querySelector('.env-value-field').getBoundingClientRect()
        const remove = el.querySelector('.env-delete').getBoundingClientRect()
        return { sameRow: Math.abs(key.y + key.height / 2 - value.y - value.height / 2) <= 1,
          inside: key.x >= box.x && key.right <= value.x && value.right <= remove.x && remove.right <= box.right,
          inputWidth: value.width }
      }))
      check(geometry.every(item => item.sameRow && item.inside && item.inputWidth >= 100), `${width}px卡片不应换成两行或相互覆盖：${JSON.stringify(geometry)}`)
      const overflow = await panel.evaluate(el => {
        const box = el.getBoundingClientRect()
        return { scroll: el.scrollWidth, client: el.clientWidth, children: [...el.querySelectorAll('*')].filter(child => child.getBoundingClientRect().right > box.right + 1).map(child => ({ tag: child.tagName, class: child.className, width: child.getBoundingClientRect().width })).slice(0, 8) }
      })
      check(overflow.scroll <= overflow.client + 1 && overflow.children.length === 0, `${width}px设置面板不应横向溢出：${JSON.stringify(overflow)}`)
      check(await panel.locator('.env-table-header').count() === 0, '不保留旧表格表头')
    }
    check(await panel.locator('input[type=password]').count() === 1, '已保存值默认隐藏')
    check(await panel.getByRole('button', { name: '保存更改' }).isDisabled(), '未修改时无需保存')
    check(diskReads === 0, '打开设置不应查询磁盘或唤醒环境')
    await checkMenu()
    await panel.getByRole('button', { name: '唤醒并查询磁盘用量' }).click()
    await panel.getByText('已用 257.48 MB · 可用 1.47 GB', { exact: true }).waitFor()
    check(await panel.getByRole('button', { name: /磁盘用量/ }).count() === 0, '查询成功后不显示查询或刷新入口')
    const meter = panel.getByRole('meter', { name: '磁盘用量' })
    await meter.waitFor()
    const percentage = Number(await meter.getAttribute('aria-valuenow'))
    check(Math.abs(percentage - 269987676 / (269987676 + 1577058304) * 100) < 0.001, '容量条应按已用与可用空间绘制')
    const ratio = await meter.evaluate(el => el.querySelector('.sandbox-disk-fill').getBoundingClientRect().width / el.getBoundingClientRect().width * 100)
    check(Math.abs(ratio - percentage) < 0.2, '可见填充宽度与容量比值一致')
    await checkCards(1440)
    await panel.screenshot({ path: '/tmp/yuxi-runtime-settings-light.png' })
    await page.locator('.settings-modal:visible').screenshot({ path: '/tmp/yuxi-runtime-settings-modal.png' })
    await panel.getByRole('button', { name: '环境变量使用说明' }).focus()
    await page.getByRole('tooltip').waitFor()
    const help = panel.getByRole('button', { name: '环境变量使用说明' })
    await help.hover()
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.env-help')).backgroundColor === 'rgba(0, 0, 0, 0)')
    check(await help.evaluate(el => getComputedStyle(el).backgroundColor) === 'rgba(0, 0, 0, 0)', '生效说明 hover 不应有背景')
    await panel.getByRole('button', { name: '查看变量值' }).click()
    check(await panel.getByLabel('SILICONFLOW_API_KEY的值', { exact: true }).getAttribute('type') === 'text', '显隐按钮应切换输入类型')
    await panel.getByRole('button', { name: '隐藏变量值' }).click()
    await panel.getByRole('button', { name: '验证配置' }).click()
    await panel.getByRole('img', { name: '验证通过', exact: true }).waitFor()
    check(await panel.getByText(/已匹配|验证时间|管理账户级环境变量/).count() === 0, '成功不应产生重复说明、匹配文案或时间行')
    check(await panel.locator('.env-verification').count() === 0, '成功不应保留额外结果区域')
    await page.getByRole('tooltip').waitFor({ state: 'hidden' })
    await panel.screenshot({ path: '/tmp/yuxi-runtime-settings-verified.png' })
    mismatch = true
    await panel.getByRole('button', { name: '验证配置' }).click()
    await panel.getByText('不一致', { exact: true }).waitFor()
    await panel.screenshot({ path: '/tmp/yuxi-runtime-settings-mismatch.png' })
    await panel.getByRole('button', { name: '添加变量', exact: true }).click()
    await panel.getByRole('textbox', { name: '变量名', exact: true }).fill('EXAMPLE_TOKEN')
    await panel.getByLabel('EXAMPLE_TOKEN的值', { exact: true }).fill('second-synthetic-value')
    check(await panel.getByRole('button', { name: '保存更改' }).isEnabled(), '编辑后可以保存')
    check(await panel.getByRole('button', { name: '验证配置' }).isDisabled(), '草稿禁止验证旧保存值')
    check(await panel.getByText('不一致', { exact: true }).count() === 0, '编辑后逐项旧验证失效')
    await panel.getByRole('button', { name: '保存更改' }).click()
    await page.waitForFunction(() => document.querySelector('.agent-env-settings .env-footer-actions .ant-btn-primary')?.disabled)
    await panel.getByRole('button', { name: '刷新环境变量' }).click()
    await panel.locator('.env-key').filter({ hasText: 'EXAMPLE_TOKEN' }).waitFor()
    check(await panel.locator('input[type=password]').count() === 2, '回读保存值后仍应隐藏')
    await panel.getByRole('button', { name: '删除变量 EXAMPLE_TOKEN', exact: true }).click()
    await panel.getByRole('button', { name: '保存更改' }).click()
    await page.waitForFunction(() => document.querySelector('.agent-env-settings .env-footer-actions .ant-btn-primary')?.disabled)
    const longKey = 'LONG_' + 'NAME_'.repeat(22) + 'TOKEN'
    env[longKey] = 'long-name-synthetic-value'
    await panel.getByRole('button', { name: '刷新环境变量' }).click()
    await panel.locator('.env-key').filter({ hasText: longKey }).waitFor()
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => window.settingsStyles.setTheme(theme), theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 900 })
        await checkCards(width)
        await checkMenu()
        await panel.screenshot({ path: `/tmp/yuxi-runtime-settings-${theme}-${width}.png` })
      }
    }
    holdEnv = true
    await panel.getByRole('button', { name: '刷新环境变量' }).click()
    await panel.locator('.ant-spin-spinning .ant-spin-dot').waitFor()
    check(await panel.getByRole('button', { name: '验证配置' }).isDisabled(), '加载期间禁止验证')
    await panel.screenshot({ path: '/tmp/yuxi-runtime-settings-loading.png' })
    holdEnv = false; releaseEnv()
    await panel.locator('.ant-spin-spinning .ant-spin-dot').waitFor({ state: 'hidden' })
    status = { backend: 'container', state: 'not_created', busy: false, active_runs: [], active_operations: 0 }
    await panel.getByRole('button', { name: '刷新环境状态' }).click()
    await panel.getByText('任务独立', { exact: true }).waitFor()
    check(await panel.getByRole('button', { name: '环境操作', exact: true }).count() === 0, '临时环境不显示重建')
    env = {}
    await panel.getByRole('button', { name: '刷新环境变量' }).click()
    await panel.getByRole('textbox', { name: '变量名', exact: true }).waitFor()
    check(await panel.getByRole('button', { name: '验证配置' }).isDisabled(), '空配置禁止验证')
    unavailable = true
    await panel.getByRole('button', { name: '刷新环境状态' }).click()
    await panel.getByText('执行节点暂不可用', { exact: true }).waitFor()
    await panel.screenshot({ path: '/tmp/yuxi-runtime-settings-empty-error.png' })
    check(unknownRequests.length === 0, `未声明请求：${JSON.stringify(unknownRequests)}`)
    return { status: 'passed', widths: [1440, 1024, 768], cases: ['real settings modal', 'right aligned dropdown', 'Escape focus', 'horizontal cards', 'long keys', 'light/dark', 'conceal/reveal', 'add/save/read/delete', 'match/mismatch', 'draft invalidation', 'loading', 'empty', 'error', 'ephemeral'] }
  } finally {
    releaseEnv?.()
    await page.evaluate(saved => { window.settingsStyles?.destroy(); if (saved === null) localStorage.removeItem('theme'); else localStorage.setItem('theme', saved) }, savedTheme)
    await page.unroute('**/api/**', handler)
    if (originalViewport) await page.setViewportSize(originalViewport)
  }
}
