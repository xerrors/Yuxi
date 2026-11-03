// 通过真实 Vite 页面和受控 API 验证配置入口、详情弹窗、切库隔离及主题布局。
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const stats = { file_count: 3, pending_index_count: 1, file_status_counts: { parsed: 1, error_parsing: 1, indexed: 1 } }
  const documents = [
    { file_id: 'parsed-fixture', filename: '项目方案与指标说明.pdf', status: 'parsed', token_count: 0 },
    { file_id: 'failed-fixture', filename: '待补充扫描件.pdf', status: 'error_parsing', token_count: 0 },
    { file_id: 'indexed-fixture', filename: '历史使用指南.pdf', status: 'indexed', token_count: 1280 }
  ]
  const kb = { kb_id: 'embedding-fixture', kb_type: 'milvus', name: '知识库配置检查', can_manage: true, supports_documents: true, supports_document_read: true, stats, files: {} }
  const handler = async route => {
    check(route.request().method() === 'GET', '配置详情验收不得写入业务数据')
    const path = route.request().url().split('?')[0]
    let body = {}
    if (path.endsWith('/embedding-fixture')) body = kb
    else if (path.endsWith('/knowledge-bases') || path.endsWith('/accessible')) body = { knowledge_bases: [kb] }
    else if (path.endsWith('/types')) body = { kb_types: { milvus: {} } }
    else if (path.endsWith('/documents')) body = { items: documents, total: documents.length, stats }
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
    const { createApp, h, reactive, nextTick } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const routerSource = await (await fetch('/src/app/router/index.js')).text()
    const { createRouter, createMemoryHistory } = await import(routerSource.match(/from "([^"]*\/vue-router\.js[^"']*)"/)[1])
    const { default: Detail } = await import('/src/pages/KnowledgeBaseInfoView.vue')
    const { default: Catalog } = await import('/src/modules/knowledge/ui/KnowledgeCatalog.vue')
    const { useKnowledgeBaseStore } = await import('/src/modules/knowledge/model/knowledgeBase.js')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'fixture-only', isAdmin: () => true })
    const pinia = createPinia()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/fixture/:kbId', component: { render: () => null } }] })
    await router.push('/fixture/embedding-fixture')
    const theme = useThemeStore(pinia)
    const state = reactive({ view: 'detail' })
    const root = document.querySelector('#app').__vue_app__
    const host = document.createElement('div')
    host.id = 'embedding-warning-check'
    Object.assign(host.style, { position: 'fixed', inset: '0', zIndex: 1000, background: 'var(--gray-0)' })
    document.body.append(host)
    const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () => h(state.view === 'detail' ? Detail : Catalog)) })
    app.use(pinia)
    app.use(router)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    window.embeddingFixture = { app, host, theme, state, nextTick, store: useKnowledgeBaseStore(pinia) }
  })
  const host = page.locator('#embedding-warning-check')
  const trigger = host.getByRole('button', { name: '配置异常', exact: true })
  const dialog = page.getByRole('dialog', { name: '未配置 Embedding 模型', exact: true })
  const modalContent = dialog.locator('.ant-modal-content')
  /** 为弹窗状态超时保留失败阶段，便于定位交互回归。 */
  const waitForDialog = async (state, context) => {
    try {
      await dialog.waitFor({ state, timeout: 5000 })
    } catch (error) {
      throw new Error(`${context}：${error.message}`, { cause: error })
    }
  }
  /** 等待主题和弹窗过渡完成后采集最终画面。 */
  const settle = async () => {
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    await page.waitForFunction(() => document.getAnimations().every(animation =>
      animation.effect.getTiming().iterations === Infinity || animation.playState !== 'running'
    ))
  }
  /** 更新服务端快照投影后等待 Vue 完成 DOM 更新。 */
  const updateKnowledgeBase = async changes => {
    await page.evaluate(async value => {
      Object.assign(window.embeddingFixture.store.knowledgeBase, value)
      await window.embeddingFixture.nextTick()
    }, changes)
  }
  try {
    await trigger.waitFor()
    check(await host.locator('.file-management-info .embedding-config-trigger').count() === 1, '配置入口未放在文件管理信息区域')
    check(await host.locator('.embedding-config-warning').count() === 0, '详情页仍显示顶部配置告警')
    check(await host.getByRole('alert').filter({ hasText: '未配置 Embedding 模型' }).count() === 0, '详情页仍显示大块配置告警')
    check(await trigger.getAttribute('aria-haspopup') === 'dialog', '配置入口没有声明详情弹窗')
    check(await trigger.getAttribute('aria-expanded') === 'false', '配置弹窗默认状态错误')
    check(await dialog.count() === 0, '配置详情未经点击就已显示')
    for (const mode of ['light', 'dark']) {
      await page.evaluate(mode => window.embeddingFixture.theme.setTheme(mode), mode)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        await settle()
        check(await trigger.isVisible(), '配置入口不可见')
        const toolbar = host.locator('.file-management-info')
        check(await toolbar.evaluate(el => el.scrollWidth <= el.clientWidth + 1), '文件管理工具栏横向溢出')
        const controls = await Promise.all([
          trigger.boundingBox(),
          toolbar.getByRole('button', { name: '需关注文件状态，2 个文件', exact: true }).boundingBox(),
          toolbar.getByRole('button', { name: '查看文件统计', exact: true }).boundingBox()
        ])
        check(controls.every(Boolean), '配置、关注或统计入口缺失')
        for (let first = 0; first < controls.length; first++) {
          for (let second = first + 1; second < controls.length; second++) {
            const a = controls[first]
            const b = controls[second]
            check(a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1 || a.y + a.height <= b.y + 1 || b.y + b.height <= a.y + 1, '配置入口与文件统计按钮重叠')
          }
        }
        await host.screenshot({ path: `/tmp/yuxi-embedding-toolbar-${mode}-${width}.png`, animations: 'disabled' })
        if (mode === 'dark' && width === 1440) {
          await toolbar.screenshot({ path: '/tmp/yuxi-embedding-toolbar-dark.png', animations: 'disabled' })
        }
        await trigger.click()
        await waitForDialog('visible', `${mode}/${width} 点击配置入口后未打开详情`)
        check(await trigger.getAttribute('aria-expanded') === 'true', '打开详情后入口状态未更新')
        const content = await dialog.textContent()
        check(content.includes('文件可以上传和解析'), '详情没有说明可用能力')
        check(content.includes('无法入库或进行向量检索'), '详情没有说明配置影响')
        check(content.includes('请联系管理员'), '详情没有恢复方向')
        await settle()
        check(await modalContent.evaluate(el => {
          const bounds = el.getBoundingClientRect()
          return bounds.left >= 0 && bounds.right <= innerWidth + 1 && el.scrollWidth <= el.clientWidth + 1
        }), '配置详情弹窗横向溢出')
        await page.screenshot({ path: `/tmp/yuxi-embedding-dialog-${mode}-${width}.png`, animations: 'disabled' })
        if (mode === 'dark' && width === 1440) {
          await modalContent.screenshot({ path: '/tmp/yuxi-embedding-dialog-dark.png', animations: 'disabled' })
        }
        await dialog.locator('.ant-modal-footer').getByRole('button').click()
        await waitForDialog('hidden', `${mode}/${width} 点击关闭后详情仍显示`)
      }
    }
    await trigger.focus()
    await page.keyboard.press('Enter')
    await waitForDialog('visible', 'Enter 激活配置入口后未打开详情')
    await page.waitForFunction(() =>
      document.activeElement?.closest('[role="dialog"]')?.textContent.includes('未配置 Embedding 模型'),
    null, { timeout: 5000 }).catch(error => { throw new Error(`配置详情未自然接管键盘焦点：${error.message}`, { cause: error }) })
    await page.keyboard.press('Escape')
    await waitForDialog('hidden', 'Escape 后配置详情仍显示')
    await page.waitForFunction(() => document.activeElement?.classList.contains('embedding-config-trigger'), null, { timeout: 5000 })
      .catch(error => { throw new Error(`键盘关闭后焦点未归还配置入口：${error.message}`, { cause: error }) })
    check(await trigger.getAttribute('aria-expanded') === 'false', '键盘关闭后入口状态未恢复')

    await updateKnowledgeBase({ can_manage: false })
    check(await host.getByRole('button', { name: '配置知识库', exact: true }).count() === 0, '只读用户仍显示编辑配置按钮')
    await trigger.click()
    await waitForDialog('visible', '只读用户点击配置入口后未打开详情')
    check((await dialog.textContent()).includes('请联系管理员'), '只读用户无法查看配置详情')
    await dialog.locator('.ant-modal-close').click()
    await waitForDialog('hidden', '只读用户点击 X 后配置详情仍显示')

    await trigger.click()
    await waitForDialog('visible', '修复配置前未打开详情')
    await updateKnowledgeBase({ embedding_model_spec: 'provider:embedding' })
    await trigger.waitFor({ state: 'detached' })
    await waitForDialog('hidden', '修复模型配置后旧详情仍显示')
    for (const type of ['feishu', 'dify', 'notion']) {
      await updateKnowledgeBase({ kb_type: type, embedding_model_spec: null })
      check(await trigger.count() === 0, '外部连接器错误提示缺少本地模型')
    }
    await updateKnowledgeBase({ kb_type: 'milvus' })
    await trigger.click()
    await waitForDialog('visible', '切库前未打开配置详情')
    await updateKnowledgeBase({ kb_id: 'previous-fixture' })
    check(await trigger.count() === 0, '切库时旧配置产生错误提醒')
    await waitForDialog('hidden', '切库后旧配置详情仍显示')

    await page.evaluate(async () => {
      window.embeddingFixture.state.view = 'catalog'
      await window.embeddingFixture.nextTick()
    })
    const card = host.locator('.info-card').filter({ hasText: '知识库配置检查' })
    await card.waitFor()
    check(!(await card.textContent()).includes('未配置 Embedding 模型'), '知识库外层卡片仍显示模型告警')
    await card.screenshot({ path: '/tmp/yuxi-embedding-catalog.png', animations: 'disabled' })
    return { compactEntry: true, modalDetails: true, keyboardDismissal: true, readonlyDetails: true, configuredModelHidden: true, connectorsHidden: true, staleSnapshotHidden: true, catalogWarningRemoved: true, layouts: 6 }
  } finally {
    await page.evaluate(() => { window.embeddingFixture.app.unmount(); window.embeddingFixture.host.remove(); delete window.embeddingFixture })
    await page.unroute('**/api/**', handler)
  }
}
