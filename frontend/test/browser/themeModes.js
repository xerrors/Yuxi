// 已登录的 Vite 页面运行：playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/themeModes.js
// prettier-ignore
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = await page.evaluate(() => location.origin)
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const originalViewport = page.viewportSize()
  const themeItem = page.getByRole('menuitem').filter({ hasText: /^主题：/ })
  const setMode = async mode => page.evaluate(async value => {
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    useThemeStore().setTheme(value)
  }, mode)
  const waitDark = async dark => page.waitForFunction(value => document.documentElement.classList.contains('dark') === value, dark)
  const openMenu = async () => {
    await page.locator('.user-info-dropdown').click()
    await themeItem.waitFor()
  }
  const screenshotMenu = async mode => {
    const menu = page.locator('.ant-dropdown-menu:visible')
    const box = await menu.boundingBox()
    const docs = await menu.getByRole('menuitem', { name: '文档中心' }).boundingBox()
    // 截取操作区，排除真实账户姓名与 ID。
    await page.screenshot({ path: `/tmp/yuxi-theme-${mode}.png`, animations: 'disabled',
      clip: { x: box.x, y: docs.y, width: box.width, height: box.y + box.height - docs.y } })
  }
  try {
    await page.goto(`${origin}/dashboard?tab=feedback`)
    await page.locator('.user-info-dropdown').waitFor()
    await page.emulateMedia({ colorScheme: 'light' })
    await setMode('light')
    await openMenu()
    for (const [mode, label, dark, icon] of [
      ['dark', '深色模式', true, 'moon'],
      ['auto', '自动（跟随浏览器）', false, 'monitor'],
      ['light', '浅色模式', false, 'sun']
    ]) {
      await themeItem.click()
      await waitDark(dark)
      // 下一次点击也必须可用，避免将关闭动画中的短暂可见误判为展开。
      try {
        await themeItem.click({ trial: true, timeout: 2000 })
      } catch {
        throw new Error(`${mode} 主题点击后菜单收起`)
      }
      check(await themeItem.isVisible(), `${mode} 主题点击后菜单收起`)
      check((await themeItem.innerText()).includes(label), `${mode} 未显示当前模式`)
      check(await themeItem.locator(`.lucide-${icon}`).count() === 1, `${mode} 图标错误`)
      check(await page.evaluate(() => localStorage.getItem('theme')) === mode, `${mode} 未保存选择`)
      await screenshotMenu(mode)
    }
    await page.locator('.system-manage-view > .page-header').click({ position: { x: 250, y: 15 } })
    await themeItem.waitFor({ state: 'hidden' })
    await openMenu()
    await page.getByRole('menuitem', { name: '设置', exact: true }).click()
    await themeItem.waitFor({ state: 'hidden' })
    await page.locator('.settings-modal').waitFor()
    await page.getByRole('button', { name: '关闭设置', exact: true }).click()
    for (const mode of ['light', 'dark', 'auto']) {
      await setMode(mode)
      await page.reload()
      await page.locator('.user-info-dropdown').waitFor()
      await waitDark(mode === 'dark')
      await openMenu()
      check((await themeItem.innerText()).includes({ light: '浅色模式', dark: '深色模式', auto: '自动（跟随浏览器）' }[mode]), `${mode} 重载丢失选择`)
      await page.locator('.user-info-dropdown').click()
      await themeItem.waitFor({ state: 'hidden' })
    }
    await page.emulateMedia({ colorScheme: 'dark' })
    await waitDark(true)
    check(await page.evaluate(() => localStorage.getItem('theme')) === 'auto', '浏览器变化覆盖了自动选择')
    await page.emulateMedia({ colorScheme: 'light' })
    await waitDark(false)
    check(await page.getByRole('button', { name: /^切换主题，当前/ }).count() === 0, '数据总览不应重复提供主题快捷按钮')
    await setMode('light')
    await page.emulateMedia({ colorScheme: 'dark' })
    await waitDark(false)
    await setMode('dark')
    await page.emulateMedia({ colorScheme: 'light' })
    await waitDark(true)
    await page.setViewportSize({ width: 375, height: 812 })
    await openMenu()
    await themeItem.click()
    await waitDark(false)
    const box = await themeItem.boundingBox()
    check(box.x >= 0 && box.x + box.width <= 375, '窄屏主题菜单超出屏幕')
    await screenshotMenu('auto-mobile')
    return { cycle: true, menuStaysOpen: true, outsideCloses: true, settingsCloses: true,
      reloadModes: 3, browserPreferenceLive: true, explicitModesOverrideBrowser: true,
      dashboardShortcutRemoved: true, mobile: true }
  } finally {
    await page.emulateMedia({ colorScheme: null })
    await page.setViewportSize(originalViewport)
    await setMode(originalTheme ?? 'light')
    if (originalTheme === null) await page.evaluate(() => localStorage.removeItem('theme'))
  }
}
