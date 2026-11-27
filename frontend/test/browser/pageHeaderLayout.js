// 已登录超级管理员的独立 Vite 会话；只读取列表，不写入业务数据。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/pageHeaderLayout.js
async page => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const originalUrl = page.url()
  const viewport = page.viewportSize()
  const originalSidebar = await page.evaluate(async () => { const { useChatUIStore } = await import('/src/modules/session/model/chatUI.js'); return useChatUIStore().sidebarCollapsed })
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  /** 检查每个标签的实际命中区域，发现没有页面级溢出的局部遮挡。 */
  const checkHeader = async () => {
    const layout = await page.locator('.page-header').evaluate(header => {
      const box = header.getBoundingClientRect()
      const actions = header.querySelector('.page-header-right')?.getBoundingClientRect()
      const items = [...header.querySelectorAll('.tab-item')].map(item => {
        const rect = item.getBoundingClientRect()
        const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
        return { label: item.textContent.trim(), inside: rect.left >= box.left && rect.right <= box.right && rect.top >= box.top && rect.bottom <= box.bottom, reachable: item.contains(hit) && (!actions || actions.width === 0 || rect.right <= actions.left || rect.left >= actions.right || rect.bottom <= actions.top || rect.top >= actions.bottom) }
      })
      return { items, overflow: header.scrollWidth > header.clientWidth + 1 }
    })
    check(!layout.overflow, '页头产生横向溢出')
    for (const item of layout.items) check(item.inside && item.reachable, `页头标签被裁切或遮挡：${item.label}`)
  }
  try {
    await page.goto(`${origin}/agent-manage?tab=providers`)
    await page.locator('.model-provider-manage-panel').waitFor()
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.evaluate(async () => { const { useChatUIStore } = await import('/src/modules/session/model/chatUI.js'); useChatUIStore().sidebarCollapsed = false })
    await page.waitForFunction(() => document.querySelector('.header').getBoundingClientRect().width === 230)
    check(await page.locator('.page-header .tab-item').count() >= 4, '需要超级管理员会话覆盖全部管理标签')
    const coverage = []
    for (const mode of ['light', 'dark']) {
      await page.evaluate(async mode => {
        const { useThemeStore } = await import('/src/shared/model/theme.js')
        useThemeStore().setTheme(mode)
      }, mode)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        await checkHeader()
        await page.locator('.page-header').screenshot({ path: `/tmp/yuxi-page-header-${mode}-${width}.png` })
        coverage.push({ mode, width })
      }
    }
    // 恢复固定单行布局，必须因为标签被右侧统计遮挡而拒绝。
    const oldLayout = await page.addStyleTag({ content: '.page-header {flex-wrap:nowrap!important;height:48px!important;padding:0 18px!important}.page-header-left {flex-wrap:nowrap!important}.page-header-tabs {flex-shrink:0!important;flex-wrap:nowrap!important}.page-header-right {flex-shrink:0!important}' })
    let rejected = false
    try { await checkHeader() } catch (error) { rejected = /页头标签被裁切或遮挡|页头产生横向溢出/.test(error.message) }
    finally { await oldLayout.evaluate(element => element.remove()) }
    check(rejected, '布局检查没有拒绝旧的单行挤压问题')
    await checkHeader()
    await page.locator('.page-header').getByRole('button', { name: 'API 集成', exact: true }).click()
    await page.waitForURL('**/system-manage?tab=integration')
    await checkHeader()
    return { coverage, oldLayoutRejected: rejected, integrationTabReachable: true }
  } finally {
    await page.evaluate(async value => {
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      useThemeStore().setTheme(value ?? 'light')
      if (value === null) localStorage.removeItem('theme')
    }, originalTheme)
    if (viewport) await page.setViewportSize(viewport)
    await page.evaluate(async value => { const { useChatUIStore } = await import('/src/modules/session/model/chatUI.js'); useChatUIStore().sidebarCollapsed = value }, originalSidebar)
    await page.goto(originalUrl)
  }
}
