// 独立 Vite 会话：playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/homeEntry.js
// 固定公开配置与登录协议 fixture 验证单屏入口；不验证后端授权或智能体执行。
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const branding = {
    branding: { name: '语析', logo: '/favicon.svg', title: '让知识，成为行动。', login_bg: '/login-bg.jpg' },
    footer: { copyright: '语析 · 首页测试' }
  }
  let failHealth = false
  let holdHealth = true
  let releaseHealth
  const agent = { agent_id: 'home-fixture-agent', name: '首页验证智能体', can_run: true, is_default: true, config_json: { context: {} }, configurable_items: {}, metadata: {} }
  const healthWait = new Promise(resolve => { releaseHealth = resolve })
  const resources = []
  const executionRequests = []
  await page.addInitScript(() => {
    if (window.__homePreviewTimers) return
    const timers = { live: new Set(), ticks: 0 }
    window.__homePreviewTimers = timers
    const createInterval = window.setInterval
    const clear = window.clearInterval
    window.setInterval = function(callback, delay, ...args) {
      if (!new Error().stack.includes('/HomeAgentPreview.vue')) return createInterval.call(this, callback, delay, ...args)
      const id = createInterval.call(this, (...values) => { timers.ticks++; callback(...values) }, delay, ...args)
      timers.live.add(id)
      return id
    }
    window.clearInterval = function(id) { timers.live.delete(id); return clear.call(this, id) }
  })
  const collect = request => resources.push(request.url())
  page.on('request', collect)
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('?')[0].replace(origin, '')
    if (route.request().method() === 'POST' && /\/agents\/sessions(?:\/[^/]+\/events)?$/.test(path)) executionRequests.push(path)
    let json = { data: [], files: [], skills: [], projects: [], providers: [], config: {} }
    if (path.endsWith('/system/health')) {
      if (holdHealth) await healthWait
      if (failHealth) return route.fulfill({ status: 503, json: { detail: '服务暂不可用' } })
      json = { status: 'ok' }
    }
    if (path.endsWith('/system/info')) json = { success: true, data: branding }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path.endsWith('/auth/me')) json = { id: 1, uid: 'home-fixture', username: '首页测试', role: 'user' }
    if (path === '/api/agent') json = { agents: [agent] }
    if (path === '/api/agent/home-fixture-agent') json = agent
    if (path === '/api/projects') json = []
    if (path.endsWith('/agents/sessions')) json = { data: [], has_more: false }
    return route.fulfill({ json })
  })
  const waitHome = () => page.locator('#home-title').waitFor()
  const navigateHome = async () => {
    await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/'))
    await waitHome()
  }
  const layout = () => page.evaluate(() => {
    const title = document.querySelector('#home-title')
    const controls = [...document.querySelectorAll('.header-actions > *, .entry-toolbar > *, .entry-actions > *, #home-prompt')]
    const input = document.querySelector('.entry-shell').getBoundingClientRect()
    return {
      overflowX: document.documentElement.scrollWidth > innerWidth + 1,
      overflowY: document.documentElement.scrollHeight > innerHeight + 1,
      titleLines: title.clientHeight / parseFloat(getComputedStyle(title).lineHeight),
      footerVisible: document.querySelector('.site-footer').getBoundingClientRect().bottom <= innerHeight + 1,
      controlsVisible: controls.every(el => { const rect = el.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight }),
      sections: document.querySelectorAll('main section').length,
      inputCentered: Math.abs(input.left + input.width / 2 - innerWidth / 2) < 1
    }
  })
  try {
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.goto(origin)
    await page.getByRole('status').filter({ hasText: '正在连接服务' }).waitFor()
    holdHealth = false
    releaseHealth()
    await waitHome()
    check(await page.locator('.subtitle').count() === 0, '首页仍展示已移除的副标题')
    check(await page.getByRole('button', { name: '发送', exact: true }).isEnabled(), '空输入仍禁用发送入口')
    for (const empty of ['', '   \n  ']) {
      await page.locator('#home-prompt').fill(empty)
      await page.locator('#home-prompt').press('Enter')
      await page.waitForURL('**/login')
      check(!resources.some(url => url.includes('/pages/AgentView.vue')), '访客在登录跳转前下载了对话工作区')
      check(await page.evaluate(() => sessionStorage.getItem('redirect')) === '/agent' && await page.evaluate(() => sessionStorage.getItem('yuxi:session-entry-draft')) === null, '空白输入没有直接进入对话或写入了空草稿')
      await navigateHome()
    }
    await page.locator('#home-prompt').fill('')
    await page.locator('#home-title').click()
    check(await page.locator('#home-prompt').getAttribute('placeholder') === '想问点什么？', '输入提示未简化')
    check((await page.locator('.entry-model').innerText()).trim() === 'DeepSeek V4.1 Flash', '首页模型名称错误')
    check(await page.locator('.entry-model svg').count() === 1 && await page.locator('.entry-toolbar [role="combobox"], .entry-toolbar select').count() === 0, '固定模型展示缺少箭头或开放了切换')
    await page.locator('.entry-model').click()
    check(await page.getByRole('listbox').count() === 0, '固定模型展示打开了选择菜单')
    await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme('light') })
    await page.emulateMedia({ colorScheme: 'light' })
    const themeToggle = page.getByRole('button', { name: /^切换主题/ })
    await themeToggle.click()
    check(await themeToggle.getAttribute('aria-label') === '切换主题，当前深色模式' && await page.locator('html.dark').count() === 1, '主题按钮未切到深色')
    await themeToggle.click()
    check((await themeToggle.getAttribute('aria-label')).includes('自动') && await page.locator('html.dark').count() === 0, '自动主题未跟随浅色系统')
    await page.emulateMedia({ colorScheme: 'dark' })
    await page.waitForFunction(() => document.documentElement.classList.contains('dark'))
    check(await page.evaluate(() => localStorage.getItem('theme')) === 'auto', '自动主题选择没有保存')
    await themeToggle.click()
    check(await themeToggle.getAttribute('aria-label') === '切换主题，当前浅色模式' && await page.locator('html.dark').count() === 0, '主题按钮没有完成三态循环')
    const agentPreview = page.locator('.agent-preview')
    const firstAgent = await agentPreview.innerText()
    const firstAvatar = await agentPreview.locator('img').getAttribute('src')
    check(await page.evaluate(() => window.__homePreviewTimers.live.size) === 1, '轮播未只创建一个定时器')
    await page.waitForTimeout(5400)
    check(await agentPreview.innerText() !== firstAgent && await agentPreview.locator('img').getAttribute('src') !== firstAvatar, '示例智能体没有同时轮播头像和名称')
    await page.locator('#home-prompt').focus()
    const focusedAgent = await agentPreview.innerText()
    check(await page.evaluate(() => window.__homePreviewTimers.live.size) === 0, '输入聚焦后没有清理轮播定时器')
    await page.waitForTimeout(5400)
    check(await agentPreview.innerText() === focusedAgent, '输入聚焦期间智能体仍切换')
    await page.locator('#home-prompt').fill('暂存文字')
    await page.locator('#home-title').click()
    await page.waitForTimeout(5400)
    check(await agentPreview.innerText() === focusedAgent, '已有输入时智能体仍切换')
    await page.locator('#home-prompt').fill('')
    await page.locator('#home-title').click()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.waitForTimeout(5400)
    check(await agentPreview.innerText() === focusedAgent, '减少动效下智能体仍轮播')
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.waitForTimeout(5400)
    check(await agentPreview.innerText() !== focusedAgent, '退出输入后智能体未恢复轮播')
    const previewHandle = await agentPreview.elementHandle()
    const beforeNavigationAgent = await agentPreview.innerText()
    const beforeNavigationTicks = await page.evaluate(() => window.__homePreviewTimers.ticks)
    await page.getByRole('button', { name: '去对话', exact: true }).click()
    await page.waitForURL('**/login')
    await page.waitForTimeout(5400)
    check(await previewHandle.evaluate(el => el.innerText) === beforeNavigationAgent, '离开首页后智能体仍轮播')
    check(await page.evaluate(ticks => window.__homePreviewTimers.live.size === 0 && window.__homePreviewTimers.ticks === ticks, beforeNavigationTicks), '离开首页后轮播定时器仍消耗资源')
    check(await page.evaluate(() => sessionStorage.getItem('yuxi:session-entry-draft')) === null, '顶部对话入口写入了空草稿')
    await navigateHome()
    const layouts = []
    const brandStyles = {}
    const readButtonStyle = element => {
      const style = getComputedStyle(element)
      return { background: style.backgroundColor, color: style.color }
    }
    const readBrandStyle = element => {
      const style = getComputedStyle(element)
      return { family: style.fontFamily, size: style.fontSize, weight: style.fontWeight, lineHeight: style.lineHeight, color: style.color }
    }
    for (const theme of ['light', 'dark']) {
      await page.evaluate(async value => {
        const { useThemeStore } = await import('/src/shared/model/theme.js')
        useThemeStore().setTheme(value)
      }, theme)
      for (const [width, height] of [[768, 768], [1024, 768], [1280, 720], [1440, 900], [1920, 1080]]) {
        await page.setViewportSize({ width, height })
        await page.waitForTimeout(250)
        const result = await layout()
        check(!result.overflowX && !result.overflowY && result.footerVisible && result.controlsVisible && result.titleLines <= 3 && result.sections === 0 && result.inputCentered, `单屏布局失败 ${theme} ${width}x${height}: ${JSON.stringify(result)}`)
        layouts.push({ theme, width, height, ...result })
        if (width === 1440) {
          const placeholderContrast = await page.locator('#home-prompt').evaluate(element => {
            const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => value / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0)
            const foreground = luminance(getComputedStyle(element, '::placeholder').color)
            const background = luminance(getComputedStyle(element.closest('.entry-composer')).backgroundColor)
            return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
          })
          check(placeholderContrast >= 4.5, `输入提示对比不足 ${theme}: ${placeholderContrast}`)
          brandStyles[theme] = await page.locator('.brand span').evaluate(readBrandStyle)
          const expected = theme === 'light' ? { background: 'rgb(0, 0, 0)', color: 'rgb(255, 255, 255)' } : { background: 'rgb(250, 250, 250)', color: 'rgb(29, 32, 36)' }
          await page.locator('#home-prompt').fill('发送按钮状态验证')
          for (const selector of ['.primary-button', '.header-chat']) {
            const button = page.locator(selector)
            check(JSON.stringify(await button.evaluate(readButtonStyle)) === JSON.stringify(expected), `按钮黑白主题错误 ${theme} ${selector}`)
            await button.hover()
            await page.waitForTimeout(250)
            check(JSON.stringify(await button.evaluate(readButtonStyle)) === JSON.stringify(expected), `hover 恢复蓝绿色 ${theme} ${selector}`)
            await page.mouse.down()
            check(JSON.stringify(await button.evaluate(readButtonStyle)) === JSON.stringify(expected), `按下时恢复蓝绿色 ${theme} ${selector}`)
            await page.mouse.move(10, 10)
            await page.mouse.up()
          }
          await page.locator('#home-prompt').fill('')
          await page.locator('#home-title').click()
          await page.waitForTimeout(220)
        }
        if (width === 768 || width === 1440) await page.screenshot({ path: `/tmp/yuxi-home-entry-${theme}-${width}.png` })
      }
    }
    const flowCanvas = await page.locator('.knowledge-flow canvas').elementHandle()
    const image = () => flowCanvas.evaluate(el => el.toDataURL())
    const alphaMass = () => flowCanvas.evaluate(el => {
      const pixels = el.getContext('2d').getImageData(0, 0, el.width, el.height).data
      let total = 0
      for (let index = 3; index < pixels.length; index += 4) total += pixels[index]
      return total
    })
    await page.waitForTimeout(1200)
    const idleAlpha = await alphaMass()
    await page.locator('#home-prompt').focus()
    await page.waitForTimeout(1200)
    const focusedAlpha = await alphaMass()
    check(focusedAlpha < idleAlpha * 0.9, '输入聚焦时背景没有收淡')
    await page.locator('#home-title').click()
    await page.waitForTimeout(1200)
    check(await alphaMass() > focusedAlpha * 1.1, '离开输入框后背景层次没有恢复')
    const moving = await image()
    await page.waitForTimeout(250)
    check(moving !== await image(), '知识流线未实际绘制动画')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.waitForTimeout(100)
    const reduced = await image()
    await page.waitForTimeout(250)
    check(reduced === await image(), '减少动效下画布仍自动变化')
    const reducedIdleAlpha = await alphaMass()
    await page.locator('#home-prompt').focus()
    await page.waitForTimeout(100)
    check(await alphaMass() < reducedIdleAlpha * 0.9, '减少动效下输入聚焦没有立即收淡背景')
    const reducedFocused = await image()
    await page.waitForTimeout(250)
    check(reducedFocused === await image(), '减少动效下输入状态启动了动画')
    await page.locator('#home-title').click()
    await page.waitForTimeout(100)
    check(reduced === await image(), '减少动效下失焦未恢复静态背景')
    const alphaHash = () => flowCanvas.evaluate(el => {
      const pixels = el.getContext('2d').getImageData(0, 0, el.width, el.height).data
      let hash = 2166136261
      let visible = 0
      for (let index = 3; index < pixels.length; index += 4) { hash = Math.imul(hash ^ pixels[index], 16777619); if (pixels[index]) visible++ }
      return { hash, visible }
    })
    const darkGeometry = await alphaHash()
    await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme('light') })
    await page.waitForTimeout(100)
    check(JSON.stringify(darkGeometry) === JSON.stringify(await alphaHash()) && darkGeometry.visible > 5000, '浅深色没有共用同一套可见曲线几何')
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.waitForTimeout(250)
    check(reduced !== await image(), '恢复动效后画布没有继续绘制')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    check(await page.locator('.primary-button').evaluate(el => getComputedStyle(el).transitionDuration) === '0s', '减少动效未生效')
    check(!resources.some(url => /gsap|fontshare|\/home\/(providers|knowledge|multiagent|workspace)/.test(url)), '残留产品资源或动效、字体依赖')
    check(await page.locator('.header-docs').getAttribute('href') === 'https://xerrors.github.io/Yuxi/', '顶部文档入口错误')
    check(await page.locator('.hero-rule, .motion-toggle, main a, .header-docs svg').count() === 0, '保留了横杠、暂停、重复文档或外链标志')
    check(await page.locator('.entry-toolbar button').count() === 1, '主操作没有收敛为一个按钮')
    const input = page.locator('#home-prompt')
    await page.locator('#home-title').click()
    const idleBorder = await page.locator('.entry-shell').evaluate(el => getComputedStyle(el).borderColor)
    await input.fill('较长的输入用于检查换行与滚动。'.repeat(160))
    check(await input.evaluate(el => el.scrollHeight > el.clientHeight && el.scrollWidth <= el.clientWidth), '长输入无法滚动或造成水平溢出')
    await input.fill('整理知识')
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 229 })
    check(page.url() === `${origin}/`, '中文输入法确认候选词时发生导航')
    await input.press('End')
    await input.press('Shift+Enter')
    await input.pressSequentially('形成行动')
    const submittedText = await input.inputValue()
    check(submittedText === '整理知识\n形成行动', 'Shift+Enter 没有保留多行输入')
    check(await page.locator('.entry-shell').evaluate(el => getComputedStyle(el).borderColor) !== idleBorder, '输入框缺少焦点反馈')
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    const beforeLeaving = await image()
    await page.waitForTimeout(250)
    check(beforeLeaving !== await image(), '离页前画布没有运行，无法验证清理')
    await page.locator('.primary-button').focus()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    check(await page.locator('.primary-button').evaluate(el => getComputedStyle(el).outlineStyle) !== 'none', '键盘焦点不可见')
    await page.keyboard.press('Enter')
    await page.waitForURL('**/login')
    const leftImage = await image()
    await page.waitForTimeout(250)
    check(leftImage === await image(), '离页后画布仍在绘制')
    check(await page.evaluate(() => sessionStorage.getItem('redirect')) === '/agent', '登录未返回工作区')
    check(await page.evaluate(() => sessionStorage.getItem('yuxi:session-entry-draft')) === submittedText, '登录期间入口文本丢失')
    for (const theme of ['light', 'dark']) {
      await page.evaluate(async value => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(value) }, theme)
      await page.waitForFunction(color => getComputedStyle(document.querySelector('.brand-main')).color === color, brandStyles[theme].color)
      check(JSON.stringify(await page.locator('.brand-main').evaluate(readBrandStyle)) === JSON.stringify(brandStyles[theme]), `首页品牌字体与登录页不同 ${theme}`)
    }
    check(await page.locator('.brand-org, .brand-separator').count() === 0, '登录页仍拼接组织名称')
    await navigateHome()
    branding.branding.name = '测试产品'
    branding.branding.logo = '/favicon.svg?custom-product'
    await page.evaluate(() => {
      localStorage.setItem('user_token', 'home-navigation-fixture')
      localStorage.setItem('yuxi:thread-input-draft:__draft__', '已有草稿')
      localStorage.setItem('yuxi:thread-input-draft:other-thread', '另一会话的草稿')
    })
    await page.reload()
    await waitHome()
    await page.getByRole('button', { name: '去对话', exact: true }).click()
    await page.waitForURL('**/agent')
    const editor = page.locator('.agent-view [contenteditable="true"]').first()
    await editor.waitFor()
    check(!resources.some(url => /\/WidgetChart\.vue|\/AgentFilePreview\.vue|\/MessageDebugPanel\.vue|\/markdown_preview\.js|\/echarts_|virtual:model-display-metadata/.test(url)), '空白对话提前下载了排版、预览或模型展示资源')
    await page.waitForFunction(() => document.querySelector('.agent-view [contenteditable="true"]')?.innerText.includes('形成行动'))
    check((await editor.innerText()).trim() === `已有草稿\n\n${submittedText}`, '首页文本未追加到新会话已有草稿')
    check(await page.evaluate(() => sessionStorage.getItem('yuxi:session-entry-draft')) === null, '草稿交接后没有清除待接收文本')
    check(await page.evaluate(() => localStorage.getItem('yuxi:thread-input-draft:other-thread')) === '另一会话的草稿', '已有会话草稿被首页覆盖')
    await navigateHome()
    await page.locator('#home-prompt').fill('第二次入口文本')
    await page.locator('#home-prompt').press('Enter')
    await page.waitForURL('**/agent')
    await page.waitForFunction(() => document.querySelector('.agent-view [contenteditable="true"]')?.innerText.includes('第二次入口文本'))
    const combinedDraft = (await editor.innerText()).trim()
    check(combinedDraft === `已有草稿\n\n${submittedText}\n\n第二次入口文本`, '再次进入未保留草稿或重复插入入口文本')
    await navigateHome()
    check(await page.getByRole('button', { name: '发送', exact: true }).isEnabled(), '已登录空输入仍禁用发送入口')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await page.waitForURL('**/agent')
    await editor.waitFor()
    check((await editor.innerText()).trim() === combinedDraft, '空输入进入工作区重复插入历史入口文本')
    check(executionRequests.length === 0, '首页草稿未经会话页确认便执行')
    await page.evaluate(() => fetch('/api/v1/agents/sessions/home-negative/events', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'message', text: '仅请求拦截负控' }) }))
    check(executionRequests.length === 1 && executionRequests[0] === '/api/v1/agents/sessions/home-negative/events', '执行边界断言没有覆盖实际消息提交地址')
    if (await page.getByRole('button', { name: '展开侧边栏', exact: true }).count()) await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
    check(await page.locator('.brand-name').innerText() === '测试产品', '侧栏未读取产品名称')
    check(await page.locator('.sidebar-brand .brand-logo').getAttribute('src') === '/favicon.svg?custom-product', '侧栏未复用产品 Logo')
    await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
    check(await page.locator('.brand-logo-image').getAttribute('src') === '/favicon.svg?custom-product', '收起侧栏仍使用旧头像')
    await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
    await page.evaluate(() => localStorage.removeItem('user_token'))
    failHealth = true
    await page.goto(origin)
    await page.getByText('服务连接失败', { exact: true }).waitFor()
    failHealth = false
    await page.getByRole('button', { name: /重\s*试/ }).click()
    await waitHome()
    branding.branding = { name: '自定义产品', logo: '/favicon.svg?custom-product', title: '把团队知识，变成下一步行动', login_bg: '/login-bg.jpg?custom-product' }
    await page.reload()
    await waitHome()
    check(await page.locator('#home-title').innerText() === '把团队知识，\n变成下一步行动', '自定义标题丢失')
    check(await page.locator('.brand span').innerText() === '自定义产品' && await page.locator('.brand img').getAttribute('src') === '/favicon.svg?custom-product', '首页产品名称或Logo丢失')
    branding.branding.name = 'ExampleProductNameForEnterpriseDeploymentWithoutSpaces'
    await page.setViewportSize({ width: 768, height: 768 })
    await page.reload()
    await waitHome()
    check(await page.locator('.header-actions').evaluate(el => el.getBoundingClientRect().right <= innerWidth && el.getBoundingClientRect().left >= 0), '长品牌名称挤出导航')
    branding.branding.title = '团队知识与下一步行动'.repeat(14)
    await page.setViewportSize({ width: 768, height: 500 })
    await page.reload()
    await waitHome()
    check(await page.locator('.subtitle').count() === 0, '空副标题仍占用段落')
    await page.locator('.primary-button').scrollIntoViewIfNeeded()
    const longLayout = await page.evaluate(() => {
      const title = document.querySelector('#home-title').getBoundingClientRect()
      const main = document.querySelector('main').getBoundingClientRect()
      return { overflowX: document.documentElement.scrollWidth > innerWidth + 1, textInsideMain: title.top >= main.top && title.bottom <= main.bottom, height: document.documentElement.scrollHeight }
    })
    check(!longLayout.overflowX && longLayout.textInsideMain && longLayout.height > 500, '矮屏长文案未自然扩展或被裁切')
    await page.getByRole('button', { name: '去对话', exact: true }).click()
    await page.waitForURL('**/login')
    check(await page.locator('.brand-logo').getAttribute('src') === '/favicon.svg?custom-product', '登录页未使用产品 Logo')
    check(await page.locator('.login-bg-image').getAttribute('src') === '/login-bg.jpg?custom-product', '登录背景配置丢失')
    return { layouts, keyboardNavigation: true, loggedInAndOutNavigation: true, healthRetry: true, productBrandAndLogo: true, brandTypography: true, neutralButtonStates: true, neutralEntryButtons: true, longTextAndShortViewport: true, noPresentationResources: true, visualMotionAndCleanup: true, sameThemeGeometry: true, quietBackgroundOnInput: true, composedInput: true, draftHandoff: true, longProductName: true, fixedModel: true, themeCycle: true, agentAvatarCarousel: true, carouselTimerCleanup: true }
  } finally {
    releaseHealth()
    page.off('request', collect)
    await page.unrouteAll({ behavior: 'wait' })
    await page.evaluate(() => {
      localStorage.removeItem('user_token')
      localStorage.removeItem('yuxi:thread-input-draft:__draft__')
      localStorage.removeItem('yuxi:thread-input-draft:other-thread')
      sessionStorage.removeItem('redirect')
      sessionStorage.removeItem('yuxi:session-entry-draft')
    })
  }
}
