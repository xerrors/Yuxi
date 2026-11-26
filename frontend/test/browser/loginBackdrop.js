// playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/loginBackdrop.js
// 固定品牌配置，验证默认图和历史路径的自定义图片。
async originalPage => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = originalPage.url().split('/').slice(0, 3).join('/')
  const context = await originalPage.context().browser().newContext()
  let loginBg = ''
  let imageAvailable = true
  try {
    const page = await context.newPage()
    await page.route('**/api/**', route => {
      const path = route.request().url().split('?')[0].replace(origin, '')
      let json = { data: [] }
      if (path.endsWith('/system/health')) json = { status: 'ok' }
      if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '', title: '', login_bg: loginBg }, footer: {} } }
      if (path.endsWith('/check-first-run')) json = { first_run: false }
      return route.fulfill({ json })
    })
    await page.route('**/login-bg.jpg', route => imageAvailable
      ? route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>' })
      : route.fulfill({ status: 404, body: 'missing' }))
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${origin}/login`)
    const art = page.locator('.login-bg-art')
    await art.evaluate(img => img.decode())
    check(await art.getAttribute('alt') === '', '装饰图应使用空 alt')
    const lightSource = await art.evaluate(img => img.currentSrc)
    await page.evaluate(() => localStorage.setItem('theme', 'dark'))
    await page.reload()
    await page.locator('html.dark .login-bg-art').evaluate(img => img.decode())
    const darkSource = await art.evaluate(img => img.currentSrc)
    check(darkSource !== lightSource, '深色主题没有加载独立的长城插画')
    check(await art.evaluate(img => img.getAnimations().length === 0), '长城插画应保持静态')

    loginBg = '/login-bg.jpg'
    await page.reload()
    await page.waitForFunction(() => document.querySelector('.login-bg-image')?.naturalWidth === 10)
    check(await page.locator('.login-bg-art').count() === 0, '历史路径上的自定义图片被内置图覆盖')

    imageAvailable = false
    await page.reload()
    await art.evaluate(img => img.decode())
    check(await page.locator('.login-bg-image').count() === 0, '缺失历史默认图片没有显示内置图')
    await page.setViewportSize({ width: 1024, height: 800 })
    check(await art.isVisible(), '1024px 下背景图不可见')
    await page.setViewportSize({ width: 768, height: 800 })
    check(await page.locator('.card-side.is-image').isHidden(), '窄屏没有隐藏装饰图')
    check(await page.getByRole('button', { name: /登录/ }).isVisible(), '窄屏登录入口不可见')
    return { builtin: true, darkTheme: true, legacyCustomImage: true, missingLegacyFallback: true, narrow: true }
  } finally {
    await context.close()
  }
}
