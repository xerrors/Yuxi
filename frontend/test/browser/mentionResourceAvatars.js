// Vite 页面执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/mentionResourceAvatars.js
// prettier-ignore
async page => {
  const sessionsRoute = route => route.fulfill({ json: [] })
  const filesRoute = route => route.fulfill({ json: [] })
  try {
    await page.route('**/api/mention/sessions?*', sessionsRoute)
    await page.route('**/api/mention/search?*', filesRoute)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.evaluate(async () => {
      const main = await (await fetch('/src/app/main.js')).text()
      const { createApp, h, ref } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Input } = await import('/src/modules/session/ui/MessageInputComponent.vue')
      const { useUserStore } = await import('/src/modules/identity/index.js')
      const draft = ref('')
      const host = document.createElement('div')
      host.id = 'mention-avatar-check'
      host.style = 'position:fixed;inset:24px;z-index:99999;background:var(--gray-0);display:flex;align-items:flex-end;padding:24px'
      const app = createApp({ render: () => h(Input, {
        modelValue: draft.value,
        'onUpdate:modelValue': value => { draft.value = value },
        mention: {
          knowledgeBases: [{ kb_id: 'kb-fixture-1', name: 'Production deployment check', description: 'Synthetic deployment verification' }],
          skills: [
            { slug: 'mysql-reporter', name: 'Mysql Reporter', description: '生成 MySQL 查询报表。当用户需要查询 MySQL 数据库并以报表形式展示结果时使用此技能。' },
            { slug: 'chat-widgets', name: 'Chat Widgets', description: '在普通聊天中直接展示图片、图文卡片和数值图表，或用简短表单收集用户回答。' }
          ],
          agents: [{ agent_id: 'research-fixture', name: '研究助手' }]
        }
      }) })
      const root = document.querySelector('#app').__vue_app__
      app.use(root.config.globalProperties.$pinia)
      const user = useUserStore(root.config.globalProperties.$pinia)
      const originalToken = user.token
      Object.assign(app._context.components, root._context.components)
      window.__mentionAvatarCheck = { app, host, draft, user, originalToken, originalDark: document.documentElement.classList.contains('dark') }
      document.body.append(host)
      user.token = 'mention-browser-fixture'
      app.mount(host)
    })
    const editor = page.locator('#mention-avatar-check .mention-editor')
    await editor.fill('@')
    await page.locator('#mention-avatar-check .mention-popup').waitFor()
    const rows = page.locator('#mention-avatar-check .resource-item')
    await page.locator('#mention-avatar-check .mention-search-placeholder').filter({ hasText: '正在查找对话' }).waitFor({ state: 'hidden' })
    await page.getByText('暂无可引用的对话', { exact: true }).waitFor()
    if (await rows.count() !== 4) throw new Error('候选资源未完整显示')
    const colors = await page.evaluate(async () => {
      const { getExtensionIconColor } = await import('/src/shared/lib/extensionIconColor.js')
      return ['kb-fixture-1', 'mysql-reporter', 'chat-widgets'].map(id => getExtensionIconColor(id))
    })
    for (let index = 0; index < 3; index++) {
      const row = rows.nth(index)
      const color = await row.locator('.resource-avatar').evaluate(node => node.style.getPropertyValue('--resource-color'))
      if (color !== colors[index]) throw new Error('候选头像与资源卡片颜色不一致')
      if (await row.locator('svg').count() !== 1) throw new Error('资源图标缺失')
    }
    if (await rows.nth(0).locator('.lucide-database-zap').count() !== 1) throw new Error('知识库没有使用目录图标')
    if (await rows.nth(3).locator('.fallback-avatar').count() !== 1) throw new Error('智能体头像缺失')
    for (const dark of [false, true]) {
      await page.evaluate(value => document.documentElement.classList.toggle('dark', value), dark)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 900 })
        const overflow = await page.locator('#mention-avatar-check .mention-popup').evaluate(node => node.scrollWidth > node.clientWidth)
        if (overflow) throw new Error('候选弹窗横向溢出')
        const borders = await page.locator('#mention-avatar-check .mention-group-title').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).borderBottomWidth))
        if (!borders.length || borders.some(width => width !== '0px')) throw new Error('mention 分组标题仍有下划线')
        const style = await page.locator('#mention-avatar-check .mention-popup').evaluate(node => {
          const css = getComputedStyle(node)
          return { padding: css.padding, radius: css.borderRadius, shadow: css.boxShadow }
        })
        if (style.padding !== '12px' || style.radius !== '16px') throw new Error('mention 浮层留白或圆角不符')
        if (!dark && style.shadow !== 'rgba(0, 0, 0, 0.05) 0px 2px 8px 0px') throw new Error('mention 浮层未使用弱阴影')
        await page.locator('#mention-avatar-check').screenshot({ path: `/tmp/yuxi-mention-avatars-${dark ? 'dark' : 'light'}-${width}.png` })
        await page.locator('#mention-avatar-check .mention-popup').screenshot({ path: `/tmp/yuxi-mention-popup-${dark ? 'dark' : 'light'}-${width}.png` })
      }
    }
    await editor.press('ArrowDown')
    await editor.press('Enter')
    const value = await page.evaluate(() => window.__mentionAvatarCheck.draft.value)
    if (!value.includes('@skill:mysql-reporter')) throw new Error('头像改动破坏键盘插入')
    return { colors, keyboardInsertion: value, screenshots: '/tmp/yuxi-mention-avatars-*.png' }
  } finally {
    try {
      await page.evaluate(() => {
        const check = window.__mentionAvatarCheck
        if (!check) return
        check.user.token = check.originalToken
        document.documentElement.classList.toggle('dark', check.originalDark)
        try {
          check.app.unmount()
        } finally {
          check.host.remove()
          delete window.__mentionAvatarCheck
        }
      })
    } finally {
      await Promise.all([
        page.unroute('**/api/mention/sessions?*', sessionsRoute),
        page.unroute('**/api/mention/search?*', filesRoute)
      ])
    }
  }
}
