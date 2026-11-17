// 使用真实工具组件和合成设备回执验证展示，不连接或操作用户设备。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/deviceDetailRendering.js
// prettier-ignore
async originalPage => {
  const context = await originalPage.context().browser().newContext()
  const screenshots = []
  try {
    const page = await context.newPage()
    await page.goto(originalPage.url().split('/').slice(0, 3).join('/') + '/login')
    await page.waitForFunction(() => Boolean(document.querySelector('#app').__vue_app__))
    await page.evaluate(async () => {
      const source = await (await fetch('/src/app/main.js')).text()
      const { createApp, h } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Tool } = await import('/src/modules/session/ui/tools/ToolCallRenderer.vue')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const device = {
        id: 'detail-fixture', name: '演示工作站', kind: 'computer', platform: 'darwin',
        status: 'online', paused: false, capabilities: ['files', 'screen'],
        available: { files: true, screen: false }, sessions: []
      }
      const calls = [
        { name: 'session_open', args: { capabilities: ['files'] }, result: { status: 'active', capabilities: ['files'], device } },
        { name: 'device_get', args: { device_id: device.id }, result: [{ type: 'text', text: JSON.stringify(device) }] },
        { name: 'device_get', args: { device_id: 'paused-fixture' }, result: { ...device, id: 'paused-fixture', name: '演示笔记本', platform: 'win32', paused: true, available: { files: false, screen: false } } },
        { name: 'device_get', args: { device_id: 'failed-fixture' }, result: { error: '设备不可访问' } },
        { name: 'device_get', args: { device_id: 'pending-fixture' } },
        { name: 'device_get', result: { ...device, name: '设备长名称'.repeat(30) } }
      ]
      const root = document.querySelector('#app').__vue_app__
      const host = document.createElement('main')
      host.id = 'device-detail-check'
      host.style = 'position:fixed;inset:0;z-index:100;padding:32px;overflow:auto;background:var(--gray-0);color:var(--gray-800)'
      document.body.append(host)
      const app = createApp({ render: () => h('div', { style: 'max-width:880px;margin:auto' }, [
        h('h2', { style: 'margin-bottom:24px' }, '设备工具 · 展示验证'),
        ...calls.map((call, index) => h('section', { 'data-case': index, style: 'margin-bottom:16px' }, [
          h(Tool, { toolCall: call, appearance: 'timeline', defaultExpanded: index === 1 || index === 2 })
        ]))
      ]) })
      app.use(root.config.globalProperties.$pinia)
      app.use(root.config.globalProperties.$router)
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      window.__deviceDetailTheme = useThemeStore(root.config.globalProperties.$pinia)
    })
    const host = page.locator('#device-detail-check')
    const detail = host.locator('[data-case="1"]')
    await detail.locator('.tool-status-icon .lucide-monitor').waitFor()
    await detail.getByText('在线', { exact: true }).waitFor()
    await detail.getByText('文件访问、屏幕查看（暂不可用）', { exact: true }).waitFor()
    await host.locator('[data-case="2"]').getByText('已暂停', { exact: true }).waitFor()
    await host.locator('img').evaluateAll(images => Promise.all(images.map(image => image.decode())))
    if (/detail-fixture|参数:/.test(await detail.innerText())) throw new Error('诊断字段占据设备详情主区域')
    await detail.locator('summary').focus()
    await page.keyboard.press('Enter')
    if (!(await detail.innerText()).includes('detail-fixture')) throw new Error('原始回执不可展开')
    await page.keyboard.press('Enter')
    await detail.locator('.tool-header').focus()
    await page.keyboard.press('Enter')
    await detail.locator('dl').waitFor({ state: 'hidden' })
    await page.keyboard.press('Space')
    await detail.locator('dl').waitFor({ state: 'visible' })
    const failed = host.locator('[data-case="3"]')
    await failed.getByText('执行失败', { exact: true }).waitFor()
    await failed.locator('.tool-header').click()
    await failed.getByText('参数:', { exact: true }).waitFor()
    if (!(await failed.innerText()).includes('设备不可访问')) throw new Error('失败原因丢失')
    if (await failed.locator('dl').count()) throw new Error('失败回执生成了设备详情')
    await failed.locator('.tool-header').click()
    await host.locator('[data-case="4"] .tool-status-icon--running').waitFor()
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => window.__deviceDetailTheme.setTheme(theme), theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 800 })
        const overflow = await host.evaluate(node => node.scrollWidth > node.clientWidth)
        if (overflow) throw new Error(`${theme}/${width} 设备详情横向溢出`)
        const name = host.locator('[data-case="5"] .device-name')
        if (!await name.evaluate(node => node.scrollWidth > node.clientWidth && getComputedStyle(node).textOverflow === 'ellipsis'))
          throw new Error('长设备名未省略')
        const path = `/tmp/yuxi-device-detail-${theme}-${width}.png`
        await host.locator(':scope > div').screenshot({ path })
        screenshots.push(path)
      }
    }
    return { passed: true, screenshots }
  } finally {
    await context.close()
  }
}
