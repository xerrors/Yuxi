import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, h, nextTick, ssrContextKey, toRaw } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createTestServer } from '../helpers/createTestServer.js'

/** 加载完整 SFC 与实际 Pinia store；轻量渲染宿主只替代 DOM。 */
async function setup(t) {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  const apps = []
  const mount = async (path, props = {}) => {
    const { default: component } = await server.ssrLoadModule(path)
    const app = renderer.createApp({ ...component, render: () => h('div') }, props)
    app.use(pinia).use(router)
    app.provide(ssrContextKey, { modules: new Set() })
    app.mount({})
    apps.push(app)
    return { app, state: toRaw(app._instance.setupState) }
  }
  t.after(async () => {
    for (const app of apps) if (app._instance) app.unmount()
    disposePinia(pinia)
    await server.close()
    delete globalThis.localStorage
  })
  const api = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  return { server, pinia, router, mount, ...api }
}

/** 装配详情页面与检索配置组件，外部请求留给各竞态场景控制。 */
async function setupEditPage(t) {
  const fixture = await setup(t)
  const { server, pinia, router, mount, knowledgeBaseApi, queryApi, typeApi } = fixture
  globalThis.document = { addEventListener() {}, removeEventListener() {} }
  t.after(() => { delete globalThis.document })
  const { message } = await import('ant-design-vue')
  t.mock.method(message, 'success', () => {})
  const { departmentApi } = await server.ssrLoadModule('/src/apis/department_api.js')
  const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')
  t.mock.method(departmentApi, 'getDepartments', async () => [])
  t.mock.method(authApi, 'getUserAccessOptions', async () => [])
  t.mock.method(typeApi, 'getChunkPresets', async () => ({ options: [] }))
  t.mock.method(knowledgeBaseApi, 'getKnowledgeBaseInfo', async (id) => ({
    kb_id: id, kb_type: 'milvus', name: id, supports_documents: true,
    supports_document_read: true, can_manage: true, additional_params: {}, stats: {}
  }))
  t.mock.method(queryApi, 'getKnowledgeBaseQueryParams', async () => ({
    params: { options: [{ key: 'final_top_k', default: 3 }] }
  }))
  const { useKnowledgeBaseStore } = await server.ssrLoadModule('/src/modules/knowledge/model/knowledgeBase.js')
  const store = useKnowledgeBaseStore(pinia)
  t.mock.method(store, 'startAutoRefresh', () => {})
  router.addRoute({ path: '/knowledge/:kbId', component: { render: () => h('div') } })
  await router.push('/knowledge/A')
  const { state } = await mount('/src/pages/KnowledgeBaseInfoView.vue')
  const panel = await mount('/src/modules/knowledge/ui/SearchConfigPanel.vue', { kbId: 'A' })
  await setImmediate()
  state.showEditModal()
  state.editFormRef.value = { validate: async () => {} }
  state.searchConfigPanelRef.value = panel.state
  state.editForm.name = 'edited A'
  state.editShareConfig.value.read_scope = { access_level: 'user', department_ids: [], user_uids: ['reader-A'] }
  panel.state.meta.final_top_k = 5
  return { ...fixture, store, state, panel }
}

for (const phase of ['validation', 'query-save', 'base-save']) {
  test(`父配置在 ${phase} 期间切库，不写入新库或释放新库读取锁`, async (t) => {
    const { router, state, store, knowledgeBaseApi, queryApi } = await setupEditPage(t)
    let release
    const hold = () => new Promise((resolve) => { release = resolve })
    if (phase === 'validation') state.editFormRef.value.validate = hold
    const writes = []
    t.mock.method(queryApi, 'updateKnowledgeBaseQueryParams', async () =>
      phase === 'query-save' ? hold() : { message: 'success' }
    )
    t.mock.method(knowledgeBaseApi, 'updateKnowledgeBase', async (id, data) => {
      writes.push({ id, data })
      if (phase === 'base-save') await hold()
      return {}
    })
    const saving = state.handleEditSubmit()
    await setImmediate()
    assert.equal(typeof release, 'function')
    await router.push('/knowledge/B')
    await setImmediate()
    assert.equal(state.editModalVisible.value, false)
    assert.equal(state.editForm.name, '')
    assert.deepEqual(state.editShareConfig.value.read_scope.user_uids, [])
    let finishB
    t.mock.method(knowledgeBaseApi, 'getKnowledgeBaseInfo', () => new Promise((resolve) => { finishB = resolve }))
    const readingB = store.getKnowledgeBaseInfo('B')
    release({ message: 'success' })
    await saving
    assert.equal(writes.some(({ id }) => id === 'B'), false, 'A 的名称或权限不能写入 B')
    assert.equal(writes.length, phase === 'base-save' ? 1 : 0)
    if (writes.length) {
      assert.equal(writes[0].id, 'A')
      assert.equal(writes[0].data.name, 'edited A')
      assert.deepEqual(writes[0].data.share_config.read_scope.user_uids, ['reader-A'])
    }
    assert.equal(store.state.lock, true, 'A 的迟到响应不能释放 B 的读取锁')
    finishB({ kb_id: 'B', name: 'B' })
    await readingB
    assert.equal(store.knowledgeBase.kb_id, 'B')
  })
}

test('父配置保存固定提交时名称和权限，等待检索保存时的新草稿不混入提交', async (t) => {
  const { state, knowledgeBaseApi, queryApi } = await setupEditPage(t)
  let release
  t.mock.method(queryApi, 'updateKnowledgeBaseQueryParams', () => new Promise((resolve) => { release = resolve }))
  const writes = []
  t.mock.method(knowledgeBaseApi, 'updateKnowledgeBase', async (id, data) => { writes.push({ id, data }); return {} })
  const saving = state.handleEditSubmit()
  await setImmediate()
  state.editForm.name = 'new draft'
  state.editShareConfig.value.read_scope.user_uids.push('later-reader')
  release({ message: 'success' })
  await saving
  assert.equal(writes[0].id, 'A')
  assert.equal(writes[0].data.name, 'edited A')
  assert.deepEqual(writes[0].data.share_config.read_scope.user_uids, ['reader-A'])
  assert.deepEqual(state.editShareConfig.value.read_scope.user_uids, ['reader-A', 'later-reader'])
})

test('检索面板跨知识库读取各自保存参数，不发送前库的覆盖值', async (t) => {
  const { server, pinia, mount, queryApi, knowledgeBaseApi } = await setup(t)
  const defaults = { A: { final_top_k: 3 }, B: { final_top_k: 20 } }
  t.mock.method(queryApi, 'getKnowledgeBaseQueryParams', async (id) => ({ params: { options: [{ key: 'final_top_k', default: defaults[id].final_top_k }] } }))
  t.mock.method(queryApi, 'getSampleQuestions', async () => ({ questions: [] }))
  t.mock.method(knowledgeBaseApi, 'getKnowledgeBaseInfo', async (id) => ({ kb_id: id }))
  t.mock.method(queryApi, 'queryTest', async (id, query, options = {}) => [{ content: `${query}:${{ ...defaults[id], ...options }.final_top_k}` }])
  const { useKnowledgeBaseStore } = await server.ssrLoadModule('/src/modules/knowledge/model/knowledgeBase.js')
  const store = useKnowledgeBaseStore(pinia)
  store.kbId = 'A'
  await store.getKnowledgeBaseInfo('A')
  const { state } = await mount('/src/modules/knowledge/ui/QuerySection.vue')
  await setImmediate()
  state.queryText.value = '结果数量'
  await state.onQuery()
  assert.equal(state.queryResult.value[0].content, '结果数量:3')
  store.kbId = 'B'
  await store.getKnowledgeBaseInfo('B')
  await nextTick()
  await state.onQuery()
  assert.equal(state.queryResult.value[0].content, '结果数量:20')
  store.stopAutoRefresh()
})

test('飞书引用使用原文阅读能力，只有检索能力的来源保持摘录', async (t) => {
  const { mount } = await setup(t)
  const { state } = await mount('/src/modules/session/ui/ReferenceAnnotatedAnswer.vue', { message: {} })
  state.openReference({ source: { kind: 'knowledge', kb_id: 'feishu', file_id: 'node', supports_documents: false, supports_document_read: true } })
  assert.equal(state.fileOpen.value, true)
  state.fileOpen.value = false
  state.openReference({ source: { kind: 'knowledge', kb_id: 'dify', file_id: 'source', supports_documents: false, supports_document_read: false } })
  assert.equal(state.fileOpen.value, false)
  assert.equal(state.snippetOpen.value, true)
})

test('检索测试保留生产片段的分数、高亮和文件定位', async (t) => {
  const { server, pinia, mount, queryApi } = await setup(t)
  t.mock.method(queryApi, 'getSampleQuestions', async () => ({ questions: [] }))
  t.mock.method(queryApi, 'queryTest', async () => [{
    kb_id: 'kb', file_id: 'file', id: 'chunk', content: '命中正文',
    metadata: { source: 'guide.md', score: 0, score_type: 'cosine', rerank_score: 0.9, highlights: [[{ text: '命中', matched: true }]], start_line: 7, end_line: 9 }
  }])
  const { useKnowledgeBaseStore } = await server.ssrLoadModule('/src/modules/knowledge/model/knowledgeBase.js')
  useKnowledgeBaseStore(pinia).knowledgeBase = { kb_id: 'kb' }
  const { state } = await mount('/src/modules/knowledge/ui/QuerySection.vue')
  state.queryText.value = '命中'
  await state.onQuery()
  const chunk = state.queryResult.value[0]
  assert.equal(chunk.score, 0)
  assert.equal(chunk.score_type, 'cosine')
  assert.equal(chunk.rerank_score, 0.9)
  assert.deepEqual(chunk.highlights, [[{ text: '命中', matched: true }]])
  state.selectChunk(chunk)
  assert.deepEqual([state.selectedChunk.value.metadata.file_id, state.selectedChunk.value.metadata.chunk_id, state.selectedChunk.value.metadata.start_line], ['file', 'chunk', 7])
})

test('多库同名片段打开各自原文，能力不足或缺少身份时保留片段预览', async (t) => {
  const { server, pinia, mount } = await setup(t)
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const user = useUserStore(pinia)
  const { state } = await mount('/src/modules/knowledge/ui/KbResultGroupedList.vue', {
    chunks: [
      { kb_id: 'A', file_id: 'same', content: 'A正文', supports_documents: true, supports_document_read: true, metadata: { source: '同名.md', start_line: 7, end_line: 9 } },
      { kb_id: 'B', file_id: 'same', content: 'B正文', supports_document_read: true, metadata: { source: '同名.md', start_line: 20, end_line: 22 } },
      { kb_id: 'readonly', file_id: 'same', content: '仅检索', supports_document_read: false, metadata: { source: '外部.md' } },
      { kb_id: 'missing', content: '无文件身份', supports_document_read: true }
    ]
  })
  for (const [id, start] of [['A', 7], ['B', 20]]) {
    const group = state.fileGroupList.value.find((item) => item.kb_id === id)
    state.openFileChunksModal(group)
    state.openDocument(group, group.chunks[0])
    assert.equal(state.chunksModalVisible.value, false)
    assert.equal(state.documentOpen.value, true)
    assert.deepEqual([state.selectedDocument.value.kb_id, state.selectedDocument.value.file_id, state.selectedDocument.value.start_line], [id, 'same', start])
    assert.equal(state.selectedDocument.value.supports_documents, id === 'A')
    assert.equal(state.useFileDetail.value, false, '普通用户使用只读原文接口')
    user.userRole = 'admin'
    assert.equal(state.useFileDetail.value, id === 'A', '管理员仅在本地库保留完整文件详情')
    user.userRole = 'user'
    state.documentOpen.value = false
  }
  for (const id of ['readonly', 'missing']) {
    const group = state.fileGroupList.value.find((item) => item.kb_id === id)
    state.openDocument(group, group.chunks[0])
    assert.equal(state.documentOpen.value, false)
  }
})

test('正文阅读从引用行开始，窗口内容保留缩进', async (t) => {
  const { mount, queryApi } = await setup(t)
  const offsets = []
  t.mock.method(queryApi, 'openDocument', async (kb, file, offset) => {
    offsets.push(offset)
    return { start_line: offset + 1, end_line: offset + 1, content: `${offset + 1}\t  原文`, total_lines: 100 }
  })
  const { state } = await mount('/src/modules/knowledge/ui/ExternalDocumentReader.vue', { kbId: 'kb', fileId: 'file', startLine: 40, endLine: 41 })
  await setImmediate()
  assert.deepEqual(offsets, [39])
  assert.equal(state.lines.value[0].text, '  原文')
})

test('检索配置保存只确认提交快照，等待期间的新草稿仍可继续保存', async (t) => {
  const { mount, queryApi } = await setup(t)
  t.mock.method(queryApi, 'getKnowledgeBaseQueryParams', async () => ({
    params: { options: [{ key: 'final_top_k', default: 3 }] }
  }))
  const pending = []
  const saved = []
  const emitted = []
  t.mock.method(queryApi, 'updateKnowledgeBaseQueryParams', (id, payload) => {
    saved.push({ id, payload })
    return new Promise((resolve) => pending.push(resolve))
  })
  const { state } = await mount('/src/modules/knowledge/ui/SearchConfigPanel.vue', {
    kbId: 'A', onSave: (payload) => emitted.push(payload)
  })
  await setImmediate()
  state.meta.final_top_k = 5
  const firstSave = state.save({ notify: false })
  state.meta.final_top_k = 20
  pending.shift()({ message: 'success' })
  assert.equal(await firstSave, true)
  assert.equal(saved[0].payload.final_top_k, 5)
  assert.equal(state.meta.final_top_k, 20)
  assert.equal(state.hasChanges(), true, '迟到保存响应不能把未提交的新草稿标记为已保存')
  assert.equal(emitted[0].final_top_k, 5)
  const secondSave = state.save({ notify: false })
  pending.shift()({ message: 'success' })
  assert.equal(await secondSave, true)
  assert.equal(saved[1].payload.final_top_k, 20)
  assert.equal(state.hasChanges(), false)
})

test('切库销毁旧配置草稿，旧加载和保存响应不能污染新实例', async (t) => {
  const { mount, queryApi } = await setup(t)
  const loads = []
  const saves = []
  t.mock.method(queryApi, 'getKnowledgeBaseQueryParams', (id) =>
    new Promise((resolve) => loads.push({ id, resolve }))
  )
  t.mock.method(queryApi, 'updateKnowledgeBaseQueryParams', (id, payload) =>
    new Promise((resolve) => saves.push({ id, payload, resolve }))
  )
  const path = '/src/modules/knowledge/ui/SearchConfigPanel.vue'
  const old = await mount(path, { kbId: 'A' })
  loads.shift().resolve({ params: { options: [{ key: 'final_top_k', default: 3 }] } })
  await setImmediate()
  old.state.meta.final_top_k = 5
  const oldSave = old.state.save({ notify: false })
  const oldReload = old.state.loadQueryParams()
  old.app.unmount()
  const current = await mount(path, { kbId: 'B' })
  loads.pop().resolve({ params: { options: [{ key: 'final_top_k', default: 20 }] } })
  await setImmediate()
  current.state.meta.final_top_k = 21
  loads.shift().resolve({ params: { options: [{ key: 'final_top_k', default: 3 }] } })
  saves[0].resolve({ message: 'success' })
  await Promise.all([oldSave, oldReload])
  assert.equal(current.state.meta.final_top_k, 21)
  assert.equal(current.state.hasChanges(), true)
  assert.equal(saves[0].id, 'A')
  assert.equal(saves[0].payload.final_top_k, 5)
  const newSave = current.state.save({ notify: false })
  saves[1].resolve({ message: 'success' })
  await newSave
  assert.equal(saves[1].id, 'B')
  assert.equal(saves[1].payload.final_top_k, 21)
})

/** 记录调度回调，不启动真实计时器。 */
function clock(t) {
  const timers = new Map()
  let sequence = 0
  globalThis.window = {
    setTimeout: (callback) => { timers.set(++sequence, callback); return sequence },
    setInterval: (callback) => { timers.set(++sequence, callback); return sequence },
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id)
  }
  t.after(() => { delete globalThis.window })
  return timers
}

test('评估面板在读取中卸载，迟到响应不得重新启动轮询或更新结果', async (t) => {
  const { mount, evaluationApi } = await setup(t)
  const timers = clock(t)
  let complete
  t.mock.method(evaluationApi, 'listDatasets', async () => ({ message: 'success', data: [] }))
  t.mock.method(evaluationApi, 'listRuns', () => new Promise((resolve) => { complete = resolve }))
  const { app, state } = await mount('/src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue', { kbId: 'kb' })
  app.unmount()
  complete({ message: 'success', data: [{ run_id: 'late', status: 'running' }] })
  await setImmediate()
  assert.deepEqual(state.runs.value, [])
  assert.equal(timers.size, 0)
})

test('评估轮询等待当前请求完成，终态停止观察', async (t) => {
  const { mount, evaluationApi } = await setup(t)
  const timers = clock(t)
  const pending = []
  t.mock.method(evaluationApi, 'listDatasets', async () => ({ message: 'success', data: [] }))
  t.mock.method(evaluationApi, 'listRuns', () => new Promise((resolve) => pending.push(resolve)))
  const { state } = await mount('/src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue', { kbId: 'kb' })
  pending.shift()({ message: 'success', data: [{ run_id: 'run', status: 'running' }] })
  await setImmediate()
  const [id, callback] = [...timers][0]
  timers.delete(id)
  callback()
  assert.equal(pending.length, 1, '同一轮未完成时不发起第二次读取')
  assert.equal(timers.size, 0)
  pending.shift()({ message: 'success', data: [{ run_id: 'run', status: 'completed' }] })
  await setImmediate()
  assert.equal(state.runs.value[0].status, 'completed')
  assert.equal(timers.size, 0)
})

test('评估单个读取失败时，仍等另一读取结束才安排下一轮', async (t) => {
  const { mount, evaluationApi } = await setup(t)
  const timers = clock(t)
  let failDatasets = false
  let finishRun
  t.mock.method(evaluationApi, 'listDatasets', async () => {
    if (failDatasets) throw new Error('评估基准暂不可用')
    return { message: 'success', data: [] }
  })
  t.mock.method(evaluationApi, 'listRuns', () => failDatasets
    ? new Promise((resolve) => { finishRun = resolve })
    : Promise.resolve({ message: 'success', data: [{ run_id: 'run', status: 'running' }] })
  )
  const { state } = await mount('/src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue', { kbId: 'kb' })
  await setImmediate()
  failDatasets = true
  const [id, callback] = [...timers][0]
  timers.delete(id)
  callback()
  await setImmediate()
  assert.equal(timers.size, 0, '一个接口失败不能在另一接口仍挂起时安排下一轮')
  finishRun({ message: 'success', data: [{ run_id: 'run', status: 'completed' }] })
  await setImmediate()
  assert.equal(state.runs.value[0].status, 'running', '整体读取失败不能发布部分新快照')
  assert.equal(timers.size, 1)
})


test('评估创建完成时旧概览在途，合并刷新仍读取新任务并开始观察', async (t) => {
  const { mount, evaluationApi } = await setup(t)
  const timers = clock(t)
  const pending = []
  t.mock.method(evaluationApi, 'listDatasets', async () => ({ message: 'success', data: [] }))
  t.mock.method(evaluationApi, 'listRuns', () => new Promise((resolve) => pending.push(resolve)))
  const { state } = await mount('/src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue', { kbId: 'kb' })
  void state.handleRunCreated()
  void state.handleDatasetCreated()
  assert.equal(pending.length, 1, '操作完成不与旧概览并发读取')
  pending.shift()({ message: 'success', data: [] })
  await setImmediate()
  assert.equal(pending.length, 1, '旧快照无活跃任务时仍合并补读一次')
  pending.shift()({ message: 'success', data: [{ run_id: 'new', status: 'running' }] })
  await setImmediate()
  assert.equal(state.runs.value[0].run_id, 'new')
  assert.equal(timers.size, 1)
})
