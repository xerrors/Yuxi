// 通过真实 Vite 页面和受控 API 验证配置提醒、切库隔离及主题布局。
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  let kb = { kb_id: 'embedding-fixture', kb_type: 'milvus', name: '知识库配置检查', can_manage: true, supports_documents: true, supports_document_read: true, stats: {}, files: {} }
  const handler = async route => {
    const path = route.request().url().split('?')[0]
    let body = {}
    if (path.endsWith('/embedding-fixture')) body = kb
    else if (path.endsWith('/documents')) body = { items: [], total: 0, stats: {} }
    else if (path.endsWith('/sample-questions')) body = { questions: [] }
    else if (path.endsWith('/query-params')) body = { params: { options: [] } }
    else if (path.endsWith('/chunk-presets')) body = { presets: [] }
    else if (path.endsWith('/departments')) body = { departments: [] }
    else if (path.endsWith('/access-options')) body = { users: [] }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  }
  await page.unroute('**/api/**')
  await page.route('**/api/**', handler)
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const routerSource = await (await fetch('/src/app/router/index.js')).text()
    const { createRouter, createMemoryHistory } = await import(routerSource.match(/from "([^"]*\/vue-router\.js[^"']*)"/)[1])
    const { default: Detail } = await import('/src/pages/KnowledgeBaseInfoView.vue')
    const { useKnowledgeBaseStore } = await import('/src/modules/knowledge/model/knowledgeBase.js')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'fixture-only', isAdmin: () => true })
    const pinia = createPinia()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/fixture/:kbId', component: { render: () => null } }] })
    await router.push('/fixture/embedding-fixture')
    const theme = useThemeStore(pinia)
    const root = document.querySelector('#app').__vue_app__
    const host = document.createElement('div')
    host.id = 'embedding-warning-check'
    Object.assign(host.style, { position: 'fixed', inset: '0', zIndex: 1000, background: 'var(--gray-0)' })
    document.body.append(host)
    const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () => h(Detail)) })
    app.use(pinia)
    app.use(router)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    window.embeddingFixture = { app, host, theme, store: useKnowledgeBaseStore(pinia) }
  })
  const host = page.locator('#embedding-warning-check')
  const warning = host.getByRole('alert').filter({ hasText: '未配置 Embedding 模型' })
  try {
    await warning.waitFor()
    check((await warning.textContent()).includes('文件可以上传和解析'), '提醒没有说明可用能力')
    check((await warning.textContent()).includes('请联系管理员'), '提醒没有恢复方向')
    for (const mode of ['light', 'dark']) {
      await page.evaluate(mode => window.embeddingFixture.theme.setTheme(mode), mode)
      for (const width of [1440, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        check(await warning.isVisible(), '配置提醒不可见')
        check(await warning.evaluate(el => el.scrollWidth <= el.clientWidth + 1), '配置提醒横向溢出')
        await host.screenshot({ path: `/tmp/yuxi-embedding-warning-${mode}-${width}.png` })
      }
    }
    await host.getByRole('tab', { name: '检索测试' }).click()
    check(await warning.isVisible(), '切换标签后配置提醒消失')
    await page.evaluate(() => { window.embeddingFixture.store.knowledgeBase.embedding_model_spec = 'provider:embedding' })
    await warning.waitFor({ state: 'detached' })
    for (const type of ['feishu', 'dify', 'notion']) {
      await page.evaluate(type => { Object.assign(window.embeddingFixture.store.knowledgeBase, { kb_type: type, embedding_model_spec: null }) }, type)
      check(await warning.count() === 0, '外部连接器错误提示缺少本地模型')
    }
    await page.evaluate(() => { Object.assign(window.embeddingFixture.store.knowledgeBase, { kb_type: 'milvus', kb_id: 'previous-fixture' }) })
    check(await warning.count() === 0, '切库时旧配置产生错误提醒')
    return { missingModel: true, configuredModelHidden: true, connectorsHidden: true, staleSnapshotHidden: true, allTabs: true, layouts: 4 }
  } finally {
    await page.evaluate(() => { window.embeddingFixture.app.unmount(); window.embeddingFixture.host.remove(); delete window.embeddingFixture })
    await page.unroute('**/api/**', handler)
  }
}
