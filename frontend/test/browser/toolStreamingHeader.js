// 在当前分支 Vite 页面执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/toolStreamingHeader.js
// 用合成条目驱动真实组件，验证流式 header 与展开交互，不执行后端工具。
// prettier-ignore
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h, reactive } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { default: Group } = await import('/src/modules/session/ui/ToolCallsGroupComponent.vue')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const state = reactive({ active: true, entries: [
      { type: 'tool', key: 'first', toolCall: { id: 'first', name: 'read_file', args: { file_path: '/workspace/guide.md' }, result: '旧工具结果' } },
      { type: 'tool', key: 'last', toolCall: { id: 'last', name: 'web_fetch', args: { url: 'https://example.com' }, result: { title: '检查页面内容与下一步操作', url: 'https://example.com' } } }
    ] })
    const root = document.querySelector('#app').__vue_app__
    const pinia = root.config.globalProperties.$pinia
    const theme = useThemeStore(pinia)
    const originalTheme = theme.themeMode
    const host = document.createElement('div')
    host.id = 'streaming-header-check'
    host.style = 'position:fixed;inset:0;z-index:99999;background:var(--gray-0);padding:64px;color:var(--color-text);overflow:auto'
    document.body.append(host)
    const app = createApp({ render: () => h('main', { style: 'max-width:880px;margin:auto' }, [
      h('p', { style: 'font-size:14px;margin-bottom:24px' }, '正在处理你的请求'),
      h(Group, { isActive: state.active, entries: state.entries, toolCalls: state.entries.filter(entry => entry.type === 'tool').map(entry => entry.toolCall) })
    ]) })
    app.use(pinia)
    app.use(root.config.globalProperties.$router)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    window.__streamingHeader = { app, host, state, theme, originalTheme }
  })
  const host = page.locator('#streaming-header-check')
  const heading = host.locator('.tool-calls-heading')
  const button = host.locator('.tool-calls-summary')
  const panel = host.locator('.tool-calls-collapse-panel')
  const evidence = []
  try {
    await heading.getByText('检查页面内容与下一步操作', { exact: true }).waitFor()
    check(await button.getAttribute('aria-expanded') === 'false', '流式默认没有收起')
    check(!/已调用|读取文件/.test(await heading.innerText()), '流式 header 仍显示汇总或旧工具')
    check(await panel.evaluate(node => getComputedStyle(node).visibility) === 'hidden', '收起面板仍然可见')
    check(await heading.locator('.live-header').evaluate(node => node.inert), '预览保留了独立工具交互')
    await host.locator('main').screenshot({ path: '/tmp/yuxi-streaming-header-tool.png' })
    await button.click()
    check(await button.getAttribute('aria-expanded') === 'true', '点击 header 未展开过程')
    await host.locator('.tool-calls-panel').getByText('读取文件', { exact: true }).waitFor()
    await page.evaluate(() => window.__streamingHeader.state.entries.push({ type: 'reasoning', key: 'thinking', content: '先前思考\n核对页面并整理接下来的操作' }))
    await heading.getByText('核对页面并整理接下来的操作', { exact: true }).waitFor()
    check(await button.getAttribute('aria-expanded') === 'true', '新条目重置了手动展开状态')
    check(!/网页抓取|先前思考/.test(await heading.innerText()), '思考 header 未替换最新工具或泄露正文')
    await button.focus()
    await page.keyboard.press('Enter')
    check(await button.getAttribute('aria-expanded') === 'false', '键盘未收起过程')
    await button.evaluate(node => node.blur())
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { window.__streamingHeader.theme.themeMode = value }, theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 600 })
        const styles = await host.evaluate(node => {
          const live = node.querySelector('.live-header')
          const panel = getComputedStyle(node.querySelector('.tool-calls-panel'))
          return { animation: getComputedStyle(live).animationName, borderTop: panel.borderTopWidth, paddingTop: panel.paddingTop, overflow: node.scrollWidth > node.clientWidth }
        })
        check(styles.animation.includes('live-header-shimmer'), '扫光动画缺失')
        check(styles.borderTop === '0px' && styles.paddingTop === '0px', '面板保留顶部分隔或内边距')
        check(!styles.overflow, 'header 横向溢出')
        evidence.push({ theme, width, ...styles })
        await host.locator('main').screenshot({ path: `/tmp/yuxi-streaming-header-${theme}-${width}.png` })
      }
    }
    await page.emulateMedia({ reducedMotion: 'reduce' })
    check(await heading.locator('.live-header').evaluate(node => getComputedStyle(node).animationName) === 'none', '减少动态效果未关闭扫光')
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.evaluate(() => {
      const state = window.__streamingHeader.state
      state.entries.push({ type: 'tool', key: 'failed', toolCall: { id: 'failed', name: 'web_fetch', args: { url: 'https://example.com' }, status: 'error', error_message: '页面读取失败' } })
    })
    await heading.getByText('执行失败', { exact: true }).waitFor()
    await page.screenshot({ path: '/tmp/yuxi-streaming-header-error.png' })
    await page.evaluate(() => { window.__streamingHeader.state.active = false })
    await heading.getByText('推理与工具调用 · 3 个工具', { exact: true }).waitFor()
    check(await heading.locator('.live-header').count() === 0, '完成后仍显示扫光预览')
    check(await button.getAttribute('aria-expanded') === 'false', '完成后过程没有收起')
    return { evidence, clickAndKeyboard: true, latestReasoning: true, preservesExpansion: true, reducedMotion: true, error: true, completedSummary: true }
  } finally {
    await page.evaluate(() => {
      const { app, host, theme, originalTheme } = window.__streamingHeader
      app.unmount()
      host.remove()
      theme.themeMode = originalTheme
      delete window.__streamingHeader
    })
  }
}
