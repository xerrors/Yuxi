// 在真实 Vite 页面验证两个工具入口共用加载、成功与失败展示，不触发工具执行。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/toolRendererSharing.js
// prettier-ignore
async originalPage => {
  const origin = originalPage.url().split('/').slice(0, 3).join('/')
  const results = []
  for (const failure of [false, true]) {
    const context = await originalPage.context().browser().newContext()
    let release
    const pending = new Promise(resolve => { release = resolve })
    try {
      const page = await context.newPage()
      await page.route('**/src/modules/session/ui/tools/renderers/CooperationTool.vue*', async route => {
        await pending
        if (failure) await route.abort('failed')
        else await route.continue()
      })
      await page.goto(origin)
      await page.waitForFunction(() => Boolean(document.querySelector('#app').__vue_app__))
      await page.evaluate(async () => {
        const source = await (await fetch('/src/app/main.js')).text()
        const { createApp, h } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
        const { default: Tool } = await import('/src/modules/session/ui/tools/ToolCallRenderer.vue')
        const { default: Group } = await import('/src/modules/session/ui/ToolCallsGroupComponent.vue')
        const { default: Mention } = await import('/src/modules/session/ui/MentionToken.vue')
        const root = document.querySelector('#app').__vue_app__
        const call = { id: 'shared', name: 'send_message', args: { target: 'fixture' }, result: { session_id: 'fixture', status: 'received' } }
        const cooperationView = { sessions: [{ session_id: 'fixture', name: '协作验证' }], tasks: new Map(), openSession() {} }
        const host = document.createElement('main')
        host.id = 'renderer-sharing'
        host.style = 'position:fixed;inset:0;z-index:99999;padding:32px;background:var(--gray-0);color:var(--color-text)'
        document.body.append(host)
        const app = createApp({ render: () => h('div', [
          h('h2', '工具组件共享验证'),
          h('section', { id: 'single', style: 'margin:24px 0' }, [h(Tool, { toolCall: call, cooperationView })]),
          h('section', { id: 'group', style: 'margin:24px 0' }, [h(Group, { toolCalls: [call], cooperationView })]),
          h(Tool, { toolCall: { name: 'future_tool', result: '完成' } }),
          h('p', { style: 'margin-top:24px' }, ['引用：', h(Mention, { type: 'skill', value: 'research', label: '资料研究' })])
        ]) })
        app.use(root.config.globalProperties.$pinia)
        app.use(root.config.globalProperties.$router)
        Object.assign(app._context.components, root._context.components)
        app.mount(host)
      })
      await page.locator('#group .tool-calls-summary').click()
      for (const id of ['single', 'group']) await page.locator(`#${id} .tool-call-display`).waitFor()
      if (await page.locator('.mention-ref-icon svg').count() !== 1) throw new Error('Mention 图标丢失')
      await page.locator('#renderer-sharing').getByText('future_tool', { exact: true }).waitFor()
      release()
      for (const id of ['single', 'group']) {
        const selector = failure ? '.tool-renderer-unavailable' : '.cooperation-event'
        await page.locator(`#${id} ${selector}`).waitFor()
      }
      await page.setViewportSize({ width: 1024, height: 768 })
      await page.screenshot({ path: `/tmp/yuxi-renderer-sharing-${failure ? 'failure' : 'success'}.png` })
      results.push({ failure, bothEntrypoints: true, loadingCard: true, mentionIcon: true, unknownTool: true })
    } finally {
      release()
      await context.close()
    }
  }
  return results
}
