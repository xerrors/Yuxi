// 真实组件验收插件提及及加载回执，合成数据不连接设备或提交消息。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/pluginMentions.js
// prettier-ignore
async originalPage => {
  const context = await originalPage.context().browser().newContext()
  const evidence = []
  try {
    const page = await context.newPage()
    await page.route('**/api/mention/sessions*', route => route.fulfill({ json: [] }))
    await page.goto(originalPage.url().split('/').slice(0, 3).join('/') + '/login')
    await page.waitForFunction(() => Boolean(document.querySelector('#app').__vue_app__))
    await page.evaluate(async () => {
      const source = await (await fetch('/src/app/main.js')).text()
      const { createApp, h, reactive } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Input } = await import('/src/modules/session/ui/MessageInputComponent.vue')
      const { default: Tool } = await import('/src/modules/session/ui/tools/ToolCallRenderer.vue')
      const { useAgentStore } = await import('/src/modules/agents/index.js')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const { configureApiAuth } = await import('/src/apis/auth_context.js')
      configureApiAuth({ getToken: () => 'synthetic-browser-test' })
      const root = document.querySelector('#app').__vue_app__
      const agent = useAgentStore(root.config.globalProperties.$pinia)
      const plugins = [
        { id: 'abridge', name: '设备操作', description: '使用已连接的设备' },
        { id: 'custom-image', name: '自定义图像', description: '生成与编辑图片' },
        { id: 'knowledge-base', name: '知识库', description: '检索可访问知识' },
        { id: 'custom', name: '自定义插件', description: '示例插件' }
      ]
      agent.selectedAgentId = 'plugin-browser-example'
      agent.agentDetails = { 'plugin-browser-example': { agent_id: 'plugin-browser-example', configurable_items: {
        plugins: { options: plugins.map(plugin => ({ key: plugin.id, name: plugin.name })) }
      } } }
      const state = reactive({ raw: '', sent: [], mention: {
        mcps: [{ slug: 'connector-example', name: '示例连接器' }],
        plugins, skills: [{ slug: 'skill-example', name: '示例 Skill' }]
      } })
      const calls = [
        { name: 'load_plugin', args: { plugin_id: 'abridge' }, result: '插件说明正文：仅使用已授权设备。' },
        { name: 'load_plugin', args: { plugin_id: 'custom-image' }, status: 'running' },
        { name: 'load_plugin', args: { plugin_id: 'knowledge-base' }, status: 'error', error_message: '当前无权加载此插件' },
        { name: 'load_plugin', args: { plugin_id: 'unknown-plugin' }, result: '未知目录名称时仍保留稳定标识。' }
      ]
      const host = document.createElement('main')
      host.id = 'plugin-mentions-check'
      host.style = 'position:fixed;inset:0;z-index:100;padding:32px;overflow:auto;background:var(--gray-0);color:var(--gray-800)'
      document.body.append(host)
      const app = createApp({ render: () => h('div', { style: 'max-width:880px;margin:auto' }, [
        h('h2', '插件提及与加载展示'),
        ...calls.map((toolCall, index) => h('section', { 'data-case': index, style: 'margin-bottom:12px' }, [
          h(Tool, { toolCall, appearance: 'timeline' })
        ])),
        h('section', { style: 'margin-top:360px', 'data-input': '' }, [h(Input, {
          modelValue: state.raw, mention: state.mention, enableUploads: false, placeholder: '输入 @ 选择插件',
          'onUpdate:modelValue': value => { state.raw = value },
          onSend: () => state.sent.push(state.raw)
        })])
      ]) })
      app.use(root.config.globalProperties.$pinia)
      app.use(root.config.globalProperties.$router)
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      window.__pluginExample = { state, plugins, theme: useThemeStore(root.config.globalProperties.$pinia) }
    })
    const host = page.locator('#plugin-mentions-check')
    const editor = host.getByRole('textbox')
    const completed = host.locator('[data-case="0"]')
    await completed.locator('.tool-header').getByText('加载插件', { exact: true }).waitFor()
    await completed.locator('.tool-header').getByText('设备操作', { exact: true }).waitFor()
    await completed.locator('.tool-status-icon--completed .lucide-computer').waitFor()
    await host.locator('[data-case="1"] .tool-status-icon--running .rotate').waitFor()
    await host.locator('[data-case="2"] .tool-status-icon--error .lucide-book-open-text').waitFor()
    await host.locator('[data-case="2"]').getByText('执行失败', { exact: true }).waitFor()
    await host.locator('[data-case="3"] .tool-header').getByText('unknown-plugin', { exact: true }).waitFor()
    await host.locator('[data-case="3"] .lucide-puzzle').waitFor()
    await completed.locator('.tool-header').click()
    await completed.getByText('参数:', { exact: true }).waitFor()
    await completed.getByText('插件说明正文：仅使用已授权设备。', { exact: true }).waitFor()
    if (!(await completed.locator('.tool-params').innerText()).includes('abridge')) throw new Error('展开后插件参数丢失')
    await host.locator('[data-case="2"] .tool-header').click()
    await host.locator('[data-case="2"]').getByText('当前无权加载此插件', { exact: true }).waitFor()

    await editor.fill('@')
    await host.locator('.mention-group-title').getByText('插件', { exact: true }).waitFor()
    const groups = await host.locator('.mention-group-title').allTextContents()
    if (!(groups.indexOf('连接器') < groups.indexOf('插件') && groups.indexOf('插件') < groups.indexOf('Skills'))) throw new Error('插件分组顺序错误')
    for (const [label, icon] of [['设备操作', 'computer'], ['自定义图像', 'image'], ['知识库', 'book-open-text'], ['自定义插件', 'puzzle']]) {
      await host.locator('.mention-item').filter({ hasText: label }).locator(`.lucide-${icon}`).waitFor()
    }
    await editor.press('ArrowDown')
    await host.locator('.mention-item.active').getByText('设备操作', { exact: true }).waitFor()
    await editor.press('Enter')
    await editor.locator('.mention-ref-plugin .lucide-computer').waitFor()
    await editor.getByText('设备操作', { exact: true }).waitFor()
    if (await page.evaluate(() => window.__pluginExample.state.raw) !== '@plugin:abridge ') throw new Error('插件未保存稳定标识')
    await host.locator('.send-button').click()
    if (JSON.stringify(await page.evaluate(() => window.__pluginExample.state.sent)) !== JSON.stringify(['@plugin:abridge '])) throw new Error('发送事件未保留插件原文')

    await editor.fill('@图像')
    const imageCandidate = host.locator('.mention-item').filter({ hasText: '自定义图像' })
    await imageCandidate.waitFor()
    await imageCandidate.click()
    await editor.locator('.mention-ref-plugin .lucide-image').waitFor()
    if (await page.evaluate(() => window.__pluginExample.state.raw) !== '@plugin:custom-image ') throw new Error('鼠标选择未保存插件标识')
    await editor.fill('@找不到的插件')
    await page.waitForFunction(() => document.querySelectorAll('#plugin-mentions-check .mention-item').length === 0)
    await editor.fill('@')
    await host.locator('.mention-item').filter({ hasText: '设备操作' }).waitFor()
    await page.evaluate(() => { window.__pluginExample.state.mention.plugins = [] })
    await page.waitForFunction(() => ![...document.querySelectorAll('#plugin-mentions-check .mention-group-title')].some(node => node.textContent === '插件'))
    await page.evaluate(() => { window.__pluginExample.state.mention.plugins = window.__pluginExample.plugins })
    await host.locator('.mention-item').filter({ hasText: '设备操作' }).waitFor()

    for (const dark of [false, true]) {
      await page.evaluate(dark => { window.__pluginExample.theme.themeMode = dark ? 'dark' : 'light' }, dark)
      for (const width of [1440, 768]) {
        await page.setViewportSize({ width, height: 1100 })
        await editor.fill('@')
        await host.locator('.mention-item').filter({ hasText: '设备操作' }).waitFor()
        const overflow = await host.evaluate(node => node.scrollWidth > node.clientWidth || [...node.querySelectorAll('.input-box, .mention-popup, .tool-call-display')].some(item => item.scrollWidth > item.clientWidth + 1))
        if (overflow) throw new Error(`插件示例横向溢出：${dark}/${width}`)
        const path = `/tmp/yuxi-plugin-mentions-${dark ? 'dark' : 'light'}-${width}.png`
        await host.screenshot({ path })
        evidence.push({ dark, width, screenshot: path, overflow: false })
      }
    }
    return { keyboardSelection: true, mouseSelection: true, rawSend: '@plugin:abridge ', unknownSearchEmpty: true, asyncDirectory: true, toolStates: ['completed', 'running', 'error'], evidence }
  } finally { await context.close() }
}
