// 在当前分支 Vite 页面执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/toolRendering.js
// 合成回执驱动真实工具入口，验证摘要与导航；不触发后端工具执行。
// prettier-ignore
async page => {
  const errors = []
  const styleEvidence = []
  const onError = error => errors.push(error.message)
  page.on('pageerror', onError)
  try {
    await page.evaluate(async () => {
      const main = await (await fetch('/src/app/main.js')).text()
      const { createApp, h } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Tool } = await import('/src/modules/session/ui/tools/ToolCallRenderer.vue')
      const { default: Group } = await import('/src/modules/session/ui/ToolCallsGroupComponent.vue')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const calls = [
        { name: 'list_agents', result: { agents: [{ id: 'research', name: '资料研究员', description: '不应展示完整智能体描述' }] } },
        { name: 'web_fetch', args: { url: 'https://example.com/docs/guide' }, result: { title: '部署与协作指南', url: 'https://example.com/docs/guide', content: '# 不应展示完整网页正文', provider: 'tavily' } },
        { name: 'get_result', args: { input_id: 'task-1' }, result: { session_id: 'research-session', status: 'completed', terminal: true, output: '不应展示完整任务结果' } },
        { name: 'search_thread_messages', args: { query: '部署' }, result: { items: [{ title: '部署验证记录', role: 'user', content: '不应展示历史消息正文' }] } },
        { name: 'read_thread_messages', result: { title: '部署验证记录', messages: [{ role: 'assistant', content: '不应展示历史消息正文' }], tool_calls: [] } },
        { name: 'install_skill', args: { source: 'example/research', skill_names: ['research'] }, result: '不应展示内部安装路径 /workspace/skills/research' },
        { name: 'mark_for_improvement', args: { reason: '不应重复展示改进原因' }, result: { saved: true, id: 'feedback-fixture' } },
        { name: 'list_agents', result: { agents: [] } },
        { name: 'web_fetch', args: { url: 'https://example.com/unavailable' }, tool_call_result: { status: 'error', content: '不应展示错误堆栈' } },
        { name: 'web_fetch', args: { url: 'https://example.com/loading' } },
        { name: 'wait_inputs', args: { input_ids: ['task-1'] }, status: 'waiting' },
        { name: 'list_sessions', status: 'incomplete' },
        { name: 'list_sessions', result: { scope: 'project', sessions: [{ session_id: 'research-session', title: '不应展示会话列表' }] } },
        { name: 'get_result', result: { session_id: 'research-session', status: 'completed', output: '不应展示无导航上下文的完整结果' } },
        { name: 'send_message', args: { target: 'research-session' }, status: 'error', error_message: '不应展示错误堆栈' },
        { name: 'install_skill', args: { source: 'example/unavailable' }, tool_call_result: { status: 'success', content: '安装失败 (research): 不应展示完整错误详情' } },
        { name: 'list_sessions', args: { scope: 'all', offset: 10 }, result: { scope: 'all', sessions: Array.from({ length: 10 }, (_, index) => ({ session_id: `hidden-session-${index}`, title: '不应展示历史会话' })) } },
        { name: 'list_sessions', result: { sessions: [{ session_id: 'hidden-legacy', title: '不应展示旧回执会话' }] } },
        { name: 'list_sessions', result: { scope: 'project', sessions: [] } },
        { name: 'list_sessions', args: { scope: 'all' }, tool_call_result: { status: 'error', content: '不应展示会话查询堆栈' } },
        { name: 'list_sessions' }
      ].map((call, index) => ({ id: `tool-fixture-${index}`, args: {}, ...call }))
      const context = { sessions: [{ session_id: 'research-session', name: '资料研究员' }], tasks: new Map([['task-1', 'research-session']]), openSession: id => { window.__openedToolSession = id } }
      const groupCalls = ['甲', '乙'].map((label, index) => ({
        id: `group-fixture-${index}`, name: 'send_message',
        args: { target: 'research-session', content: `不应展示请求正文${label}` },
        result: { status: 'received', session_id: 'research-session' }
      }))
      const host = document.createElement('div')
      host.id = 'tool-rendering-check'
      host.style = 'position:fixed;inset:0;z-index:99999;background:var(--gray-0);overflow:auto;padding:32px;color:var(--color-text)'
      const app = createApp({ render: () => h('main', { style: 'max-width:880px;margin:auto' }, [
        h('h2', { style: 'font-size:18px;margin:0 0 20px' }, '工具执行记录'),
        ...calls.map((call, index) => h('section', { 'data-case': index, style: 'margin-bottom:16px' }, [h(Tool, {
          toolCall: call, appearance: 'timeline', defaultExpanded: true,
          cooperationView: index === 13 ? null : context
        })])),
        h('section', { 'data-case': 'grouped' }, [h(Group, { toolCalls: groupCalls, cooperationView: context })]),
        h('section', { 'data-case': 'grouped-sessions' }, [h(Group, { toolCalls: [calls[0], calls[12], calls[16]], cooperationView: context })])
      ]) })
      const root = document.querySelector('#app').__vue_app__
      const pinia = root.config.globalProperties.$pinia
      app.use(pinia)
      app.use(root.config.globalProperties.$router)
      Object.assign(app._context.components, root._context.components)
      const theme = useThemeStore(pinia)
      window.__toolCheck = { app, host, theme, originalTheme: theme.themeMode }
      document.body.append(host)
      app.mount(host)
    })
    const host = page.locator('#tool-rendering-check')
    await host.locator('[data-case="0"] .is-summary').waitFor()
    await host.locator('[data-case="2"] .event-target').waitFor()
    for (const [index, text] of [[0, '1 个可用'], [1, '部署与协作指南'], [2, '查看结果'], [3, '搜索历史消息'], [4, '部署验证记录'], [5, 'research'], [6, '记录改进线索'], [7, '0 个可用'], [8, '执行失败'], [10, '等待中'], [11, '已中断'], [12, '查看了本项目会话'], [14, '操作失败'], [15, '执行失败'], [16, '查看了历史会话'], [17, '查看了历史会话'], [18, '查看了本项目会话'], [19, '执行失败'], [20, '查看本项目会话']]) {
      await host.locator(`[data-case="${index}"]`).getByText(text, { exact: true }).waitFor()
    }
    if (await host.locator('[data-case="9"] .tool-loading').count() !== 1) throw new Error('加载状态缺失')
    if (await host.locator('[data-case="20"] .tool-loading').count() !== 1) throw new Error('会话查询未复用通用加载图标')
    if (await host.locator('[data-case="19"] .tool-error').count() !== 1) throw new Error('会话查询未复用通用失败图标')
    if (await host.locator('[data-case="13"] button').count()) throw new Error('无导航上下文却显示会话按钮')
    const target = host.locator('[data-case="2"] .event-target')
    await target.focus()
    await page.keyboard.press('Enter')
    if (await page.evaluate(() => window.__openedToolSession) !== 'research-session') throw new Error('会话入口归属错误')
    for (const [index, count] of [[12, 1], [16, 10], [17, 1], [18, 0]]) {
      const list = host.locator(`[data-case="${index}"]`)
      await list.getByText(`${count} 个`, { exact: true }).waitFor()
      if (await list.locator('button, [aria-expanded], .event-target, .event-session-list').count()) throw new Error('会话摘要仍有名单或展开入口')
    }
    const group = host.locator('[data-case="grouped"]')
    await group.locator('.tool-calls-summary').click()
    await group.locator('.event-target').waitFor()
    if (await group.locator('.cooperation-event').count() !== 1) throw new Error('连续协作调用未合并')
    const groupedSessions = host.locator('[data-case="grouped-sessions"]')
    await groupedSessions.locator('.tool-calls-summary').click()
    await groupedSessions.getByText('查看了历史会话', { exact: true }).waitFor()
    if (await groupedSessions.locator('.tool-call-display.is-timeline.is-summary').count() !== 3) throw new Error('真实工具分组未共用通用摘要')
    if (await groupedSessions.locator('.cooperation-event, .tool-content, .tool-expand-icon').count()) throw new Error('分组会话查询保留了特制布局或展开区域')
    await host.locator('[data-case="1"] .tool-header').click()
    const text = await host.innerText()
    if (/不应|原始结果|调用详情/.test(text)) throw new Error('工具摘要重复展示了正文或原始结果')
    if (await host.locator('.tool-content, .tool-expand-icon, details, .yk-markdown-preview').count()) throw new Error('摘要仍有多余的结果展开区域')
    if (await host.locator('.is-summary [role="button"], .is-summary [aria-expanded], .is-summary [tabindex]').count()) throw new Error('摘要保留了无效的交互入口')
    for (const dark of [false, true]) {
      await page.evaluate(value => { window.__toolCheck.theme.themeMode = value ? 'dark' : 'light' }, dark)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        await page.mouse.move(1, 1)
        await host.evaluate(node => { node.scrollTop = 0 })
        if (await host.evaluate(node => node.scrollWidth > node.clientWidth)) throw new Error('工具展示横向溢出')
        const styles = await host.evaluate(node => {
          const properties = ['color', 'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'display', 'alignItems', 'gap', 'padding', 'margin', 'height']
          const sample = root => {
            const result = {}
            for (const selector of ['.tool-header', '.sep-header', '.note', '.description', '.tool-loader']) {
              const element = root.querySelector(selector)
              if (!element) throw new Error(`通用摘要缺少 ${selector}`)
              const style = getComputedStyle(element)
              result[selector] = Object.fromEntries(properties.map(property => [property, style[property]]))
            }
            const header = root.querySelector('.tool-header').getBoundingClientRect()
            const note = root.querySelector('.note').getBoundingClientRect()
            const description = root.querySelector('.description').getBoundingClientRect()
            result.spacing = { iconToLabel: Math.round((note.left - header.left) * 100) / 100, labelToCount: Math.round((description.left - note.right) * 100) / 100, rowHeight: header.height }
            return result
          }
          const baseline = sample(node.querySelector('[data-case="0"]'))
          const roots = [12, 16, 17, 18].map(index => node.querySelector(`[data-case="${index}"]`))
          roots.push(...node.querySelectorAll('[data-case="grouped-sessions"] .tool-call-container'))
          for (const root of roots) if (JSON.stringify(sample(root)) !== JSON.stringify(baseline)) throw new Error('会话查询与查看智能体的实际颜色、字体或布局间距不同')
          return { baseline, matchedRows: roots.length }
        })
        styleEvidence.push({ theme: dark ? 'dark' : 'light', width, ...styles })
        await host.screenshot({ path: `/tmp/yuxi-session-directory-shared-${dark ? 'dark' : 'light'}-${width}.png` })
        await groupedSessions.screenshot({ path: `/tmp/yuxi-session-directory-shared-group-${dark ? 'dark' : 'light'}-${width}.png` })
      }
    }
    if (errors.length) throw new Error(errors.join('\n'))
    return { cases: 21, themes: 2, widths: [1440, 1024, 768], groupedMessages: 2, summaryOnly: true, scopedSessions: true, legacySessions: true, sharedSummaryStyles: styleEvidence, errors }
  } finally {
    await page.evaluate(() => {
      const check = window.__toolCheck
      if (!check) return
      check.app.unmount()
      check.host.remove()
      check.theme.themeMode = check.originalTheme
      delete window.__toolCheck
      delete window.__openedToolSession
    })
    page.off('pageerror', onError)
  }
}
