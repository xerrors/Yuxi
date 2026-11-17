// 真实 Vite 组件验收：playwright-cli -s=sandbox-timing-verified run-code --filename=frontend/test/browser/sandboxTiming.js
// prettier-ignore
async page => {
  page.setDefaultTimeout(30000)
  const check = (value, message) => { if (!value) throw new Error(message) }
  await page.setViewportSize({ width: 1440, height: 900 })
  let requests = 0
  let diskReads = 0
  let diskFail = false
  let rebuildCalls = 0
  let status = { state: 'running', busy: false, error: null, active_runs: [], active_operations: 0,
    generation: 1,
    server_time: '2026-10-15T02:03:00Z', last_active_at: '2026-10-15T02:00:00Z',
    idle_deadline_at: '2026-10-15T02:05:00Z', idle_timeout_seconds: 300, paused_at: null }
  let fail = false
  await page.route('**/api/user/sandbox', route => {
    requests++
    return route.fulfill({ status: fail ? 503 : 200, contentType: 'application/json', body: JSON.stringify(fail ? { detail: { code: 'fixture_unavailable', message: '执行节点暂不可用' } } : status) })
  })
  await page.route('**/api/user/sandbox/disk-usage', route => {
    diskReads++
    status = { ...status, state: 'running', paused_at: null,
      last_active_at: '2026-10-15T02:05:00Z', idle_deadline_at: '2026-10-15T02:10:00Z', server_time: '2026-10-15T02:05:00Z' }
    return route.fulfill({ status: diskFail ? 502 : 200, contentType: 'application/json', body: JSON.stringify(diskFail ? { detail: { code: 'fixture_disk_failed', message: '磁盘用量读取失败' } } : { used_bytes: 2147483648, available_bytes: 6442450944 }) })
  })
  await page.route('**/api/user/sandbox/rebuild', route => {
    rebuildCalls++
    return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'fixture: rebuild blocked' }) })
  })
  await page.waitForFunction(() => !!document.querySelector('#app')?.__vue_app__, null, { timeout: 10000 })
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const vueUrl = main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1]
    const { createApp, h } = await import(vueUrl)
    const { default: Card } = await import('/src/modules/settings/ui/UserSandboxSettingsCard.vue')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    const { useUserStore } = await import('/src/modules/identity/model/user.js')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    configureApiAuth({ getToken: () => 'sandbox-browser-fixture' })
    const root = document.querySelector('#app').__vue_app__
    const theme = useThemeStore(root.config.globalProperties.$pinia)
    const previousTheme = theme.themeMode
    const savedTheme = localStorage.getItem('theme')
    theme.setTheme('light')
    const host = document.createElement('div')
    host.id = 'sandbox-timing-check'
    Object.assign(host.style, { position: 'fixed', inset: '0', zIndex: 900, padding: '32px', overflow: 'auto', background: 'var(--gray-0)', fontFamily: 'var(--font-family)' })
    document.body.append(host)
    const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () => h('div', { style: 'max-width:760px;margin:32px auto' }, [h(Card)])) })
    app.use(root.config.globalProperties.$pinia)
    Object.assign(app._context.components, root._context.components)
    const timers = new Set()
    const set = window.setInterval, clear = window.clearInterval
    window.setInterval = (...args) => { const id = set(...args); timers.add(id); return id }
    window.clearInterval = id => { timers.delete(id); clear(id) }
    app.mount(host)
    window.__sandboxTiming = { app, host, timers, set, clear,
      theme, previousTheme, savedTheme,
      restoreAuth: () => configureApiAuth({ getToken: () => useUserStore().token }) }
  })
  const root = page.locator('#sandbox-timing-check')
  const timing = root.locator('.sandbox-timing')
  const checkAlignment = async () => {
    const layout = await root.evaluate(el => {
      const panel = el.querySelector('.sandbox-settings').getBoundingClientRect()
      const status = el.querySelector('.sandbox-state-row').getBoundingClientRect()
      const disk = el.querySelector('.sandbox-disk').getBoundingClientRect()
      const header = el.querySelector('.sandbox-header').getBoundingClientRect()
      const actions = el.querySelector('.settings-header-actions').getBoundingClientRect()
      return { panelWidth: panel.width, statusRight: status.right, diskLeft: disk.left,
        sameRow: Math.abs(status.y + status.height / 2 - disk.y - disk.height / 2) < 1,
        stacked: disk.y >= status.bottom, actionsOffset: Math.abs(header.right - actions.right) }
    })
    check(layout.actionsOffset <= 1 && (layout.sameRow ? layout.statusRight <= layout.diskLeft : layout.stacked),
      `状态与磁盘分组清楚，操作靠右且没有相互覆盖：${JSON.stringify(layout)}`)
  }
  const checkStateAppearance = async (icon, tone) => {
    const state = root.locator('.sandbox-state-value')
    const stateIcon = root.locator('.sandbox-state-icon')
    await stateIcon.locator(`svg.lucide-${icon}[aria-hidden="true"]`).waitFor()
    check(await stateIcon.locator(`svg.lucide-${icon}[aria-hidden="true"]`).count() === 1, `${tone}状态应有装饰性图标`)
    const appearance = await state.evaluate((el, tone) => {
      const style = getComputedStyle(el)
      const sample = document.createElement('span')
      sample.style.color = `var(${tone})`
      el.append(sample)
      const expected = getComputedStyle(sample).color
      sample.remove()
      const rgb = color => color.match(/[\d.]+/g).map(Number)
      const background = rgb(getComputedStyle(el.closest('.sandbox-settings')).backgroundColor)
      const foreground = rgb(style.color)
      const alpha = foreground[3] ?? 1
      const luminance = channels => channels.slice(0, 3).reduce((sum, channel, index) => {
        const value = channel / 255
        return sum + (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][index]
      }, 0)
      const front = luminance(foreground.slice(0, 3).map((value, index) => value * alpha + background[index] * (1 - alpha)))
      const back = luminance(background)
      const contrast = (Math.max(front, back) + 0.05) / (Math.min(front, back) + 0.05)
      return { color: style.color, expected, background, contrast, border: style.borderTopWidth }
    }, tone)
    check(appearance.color === appearance.expected && appearance.border === '0px' && appearance.contrast >= 4.5,
      `状态应使用主题语义色，无边框，文字对比度至少4.5:1：${JSON.stringify(appearance)}`)
  }
  const refresh = async () => { await root.getByRole('button', { name: '刷新环境状态' }).click() }
  try {
    await timing.filter({ hasText: '预计 2 分钟 后休眠' }).waitFor()
    await checkStateAppearance('circle-play', '--color-success-900')
    const initialRequests = requests
    const initialTiming = await timing.innerText()
    await page.waitForTimeout(2200)
    check((await timing.innerText()) !== initialTiming, '本地时钟应更新倒计时')
    check(requests === initialRequests, '每秒更新时长不能每秒请求后端')
    status = { ...status, state: 'paused', paused_at: '2026-10-15T02:00:00Z', server_time: '2026-10-15T02:05:00Z', idle_deadline_at: null }
    await timing.filter({ hasText: '5 分钟' }).waitFor()
    await checkStateAppearance('moon', '--color-text-secondary')
    await root.locator('.sandbox-settings').screenshot({ path: 'tmp/sandbox-status-paused.png' })
    await checkAlignment()
    status = { ...status, state: 'resuming' }
    await refresh()
    await checkStateAppearance('loader-circle', '--color-info-900')
    status = { ...status, state: 'paused' }
    await refresh()
    await timing.filter({ hasText: '5 分钟' }).waitFor()
    check((await root.locator('.sandbox-state-content').getAttribute('title')) === '休眠于 2026-10-15 10:00:00', '悬停应显示准确休眠时间')
    check(await root.getByText('重启环境', { exact: true }).count() === 0, '删除不支持的重启入口')
    const more = root.getByRole('button', { name: '环境操作' })
    check(await more.getAttribute('aria-haspopup') === 'menu', '操作按钮应声明菜单语义')
    check(await more.getAttribute('aria-expanded') === 'false', '菜单初始收起')
    await more.focus()
    await more.press('Enter')
    const item = page.getByRole('menuitem', { name: '重建环境' })
    await item.waitFor()
    await page.waitForFunction(() => {
      const panel = document.querySelector('.ant-dropdown:not(.ant-dropdown-hidden) .action-dropdown-panel')?.getBoundingClientRect()
      const trigger = document.querySelector('#sandbox-timing-check [aria-label="环境操作"]')?.getBoundingClientRect()
      return panel && trigger && Math.abs(panel.right - trigger.right) <= 1
    })
    const menuBox = await page.locator('.action-dropdown-panel:visible').boundingBox()
    const triggerBox = await more.boundingBox()
    check(Math.abs(menuBox.x + menuBox.width - triggerBox.x - triggerBox.width) <= 1, '环境菜单右边缘必须与触发按钮对齐')
    check(await more.getAttribute('aria-expanded') === 'true', '按钮应报告菜单展开')
    check(await page.getByRole('menuitem').count() === 1, '菜单只保留重建环境')
    await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'menuitem', null, { timeout: 5000 })
    await item.press('Escape')
    check(await more.getAttribute('aria-expanded') === 'false', 'Escape应收起菜单')
    check(await more.evaluate(el => el === document.activeElement), 'Escape应恢复触发按钮焦点')
    await more.press('Enter')
    await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'menuitem', null, { timeout: 5000 })
    await item.press('Enter')
    const confirmation = page.getByRole('dialog')
    await confirmation.waitFor()
    check(await more.getAttribute('aria-expanded') === 'false', '选择操作后菜单应收起')
    await item.waitFor({ state: 'hidden' })
    check((await confirmation.innerText()).includes('工作区文件与技能将保留'), '重建确认必须说明保留范围')
    await confirmation.getByRole('button', { name: /^取\s*消$/ }).click()
    check(rebuildCalls === 0, '取消确认不能触发重建')
    check(diskReads === 0, '状态与时长刷新不能读取磁盘或唤醒环境')
    await root.getByRole('button', { name: '唤醒并查询磁盘用量' }).click()
    await root.getByText('已用 2 GB · 可用 6 GB', { exact: true }).waitFor()
    check(diskReads === 1, '手动查询只读取一次磁盘')
    check(await root.getByRole('button', { name: /磁盘用量/ }).count() === 0, '查询后不保留查询或刷新按钮')
    await root.locator('.sandbox-settings').screenshot({ path: 'tmp/sandbox-layout-light.png' })
    await more.click()
    await item.waitFor()
    const panel = await root.locator('.sandbox-settings').boundingBox()
    const menu = await item.boundingBox()
    await page.screenshot({ path: 'tmp/sandbox-layout-menu.png', clip: { ...panel,
      width: Math.max(panel.width, menu.x + menu.width - panel.x) + 12,
      height: Math.max(panel.height, menu.y + menu.height - panel.y) + 12 } })
    await more.click()
    await root.screenshot({ path: 'tmp/sandbox-timing-light.png' })
    await page.evaluate(() => window.__sandboxTiming.theme.setTheme('dark'))
    await checkStateAppearance('circle-play', '--color-success-900')
    await root.screenshot({ path: 'tmp/sandbox-timing-dark.png' })
    for (const width of [1024, 768]) {
      await page.setViewportSize({ width, height: 900 })
      check(await root.evaluate(el => el.scrollWidth <= el.clientWidth), `${width}px不能横向溢出`)
      check(await root.locator('.sandbox-state-value').evaluate(el => el.getBoundingClientRect().height < 30), `${width}px状态标签保持一行`)
      await checkAlignment()
      await root.screenshot({ path: `tmp/sandbox-timing-${width}.png` })
    }
    await root.locator('.sandbox-settings').screenshot({ path: 'tmp/sandbox-layout-dark.png' })
    status = { ...status, state: 'resuming' }
    await refresh()
    await root.locator('.sandbox-state-value').filter({ hasText: '恢复中' }).waitFor()
    await checkStateAppearance('loader-circle', '--color-info-900')
    await root.locator('.sandbox-settings').screenshot({ path: 'tmp/sandbox-status-resuming.png' })
    status = { ...status, state: 'running', generation: 2 }
    await refresh()
    diskFail = true
    await root.getByRole('button', { name: '查看磁盘用量' }).click()
    await root.getByText('磁盘用量读取失败', { exact: true }).waitFor()
    check(await root.getByText('已用 2 GB · 可用 6 GB', { exact: true }).count() === 0, '失败不能保留旧磁盘数值')
    status = { ...status, state: 'paused', paused_at: null }
    await refresh()
    await timing.filter({ hasText: '时间未记录' }).waitFor()
    status = { ...status, state: 'running', busy: true }
    await refresh()
    await timing.filter({ hasText: '任务或操作进行中，暂不休眠' }).waitFor()
    await more.click()
    await item.waitFor()
    check(await item.isDisabled(), '忙碌时重建菜单项不可用')
    await more.click()
    status = { ...status, busy: false, idle_deadline_at: '2026-10-15T02:05:00Z', server_time: '2026-10-15T02:05:01Z' }
    await refresh()
    await timing.filter({ hasText: '已达到空闲时限，等待休眠' }).waitFor()
    check((await root.locator('.sandbox-state-value').innerText()).trim() === '运行中', '倒计时归零不能冒充已休眠')
    status = { ...status, state: 'failed' }
    await refresh()
    await root.locator('.sandbox-state-value').filter({ hasText: '环境异常' }).waitFor()
    await checkStateAppearance('circle-alert', '--color-error-700')
    await root.locator('.sandbox-settings').screenshot({ path: 'tmp/sandbox-status-failed.png' })
    fail = true
    await refresh()
    await root.getByText('执行节点暂不可用', { exact: true }).waitFor()
    await checkStateAppearance('circle-question-mark', '--color-text-secondary')
    check(await timing.count() === 0, '查询失败必须清除旧时间')
    await root.screenshot({ path: 'tmp/sandbox-timing-error.png' })
    await page.evaluate(() => window.__sandboxTiming.app.unmount())
    const afterUnmount = requests
    await page.waitForTimeout(16000)
    check(requests === afterUnmount, '卸载后不能继续查询')
    check(await page.evaluate(() => window.__sandboxTiming.timers.size === 0), '卸载后必须清理两个定时器')
    return { status: 'passed', cases: ['running', 'local tick', 'automatic paused refresh', 'one-line status', 'keyboard menu', 'cancel rebuild', 'busy rebuild disabled', 'manual disk query only', 'disk read failure', 'unknown time', 'busy', 'deadline elapsed', 'error', 'unmount'], widths: [1440, 1024, 768], screenshots: ['tmp/sandbox-layout-light.png', 'tmp/sandbox-layout-menu.png', 'tmp/sandbox-layout-dark.png', 'tmp/sandbox-timing-1024.png', 'tmp/sandbox-timing-768.png', 'tmp/sandbox-timing-error.png'] }
  } finally {
    await page.evaluate(() => {
      const value = window.__sandboxTiming
      value.app.unmount(); value.host.remove(); value.restoreAuth()
      window.setInterval = value.set; window.clearInterval = value.clear
      value.theme.setTheme(value.previousTheme)
      if (value.savedTheme === null) localStorage.removeItem('theme')
      else localStorage.setItem('theme', value.savedTheme)
      delete window.__sandboxTiming
    })
    await page.unroute('**/api/user/sandbox')
    await page.unroute('**/api/user/sandbox/disk-usage')
    await page.unroute('**/api/user/sandbox/rebuild')
  }
}
