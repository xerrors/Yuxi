import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test, { before, after, afterEach } from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, h, reactive, ssrContextKey, toRef } from 'vue'
import { routerKey, routeLocationKey } from 'vue-router'
import { createTestServer } from '../helpers/createTestServer.js'

const tablePath = '../../src/modules/knowledge/ui/FileTable.vue'
let fixtureServer
let tableComponent
let pageComponent
const mounted = []
const node = () => ({ children: [], parent: null })
const renderer = createRenderer({
  createElement: node, createText: node, createComment: node,
  insert(child, parent) { child.parent = parent; parent.children.push(child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText() {}, setElementText() {}, parentNode: (n) => n.parent, nextSibling: () => null, patchProp() {}
})

before(async () => {
  fixtureServer = await createTestServer({ server: { middlewareMode: true, hmr: false }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'file-entry-boundaries', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue') return '\0file-entry-ant' },
    load(id) {
      if (id === '\0file-entry-ant') return 'export const message = new Proxy({}, { get: (_, key) => (...args) => globalThis.__fileEntry.message[key](...args) }); export const Modal = {}'
      if (id.endsWith('/src/modules/knowledge/model/knowledgeBase.js')) return 'export const useKnowledgeBaseStore = () => globalThis.__fileEntry.store'
      if (id.endsWith('/src/modules/background-jobs/index.js') || id.endsWith('/src/modules/background-jobs/model/jobs.js')) return 'export const useBackgroundJobsStore = () => globalThis.__fileEntry.jobsStore'
      if (id.endsWith('/src/modules/identity/index.js')) return 'export const useUserStore = () => globalThis.__fileEntry.userStore'
      if (id.endsWith('/src/modules/settings/index.js')) return 'export const useConfigStore = () => ({ config: {} })'
      if (id.endsWith('/src/apis/knowledge_api.js')) return 'export const documentApi = new Proxy({}, { get: (_, key) => (...args) => globalThis.__fileEntry.documentApi[key](...args) }); export const graphBuildApi = new Proxy({}, { get: (_, key) => (...args) => globalThis.__fileEntry.graphBuildApi[key](...args) })'
      if (id.endsWith('/src/modules/knowledge/model/useChunkPresetOptions.js')) return 'import { ref } from "vue"; export const useChunkPresetOptions = () => ({ chunkPresetSelectOptions: ref([]), chunkPresetLoading: ref(false), loadChunkPresetOptions: () => {}, getChunkPresetDescription: () => "" })'
      if (id.endsWith('/src/apis/department_api.js')) return 'export const departmentApi = { getDepartments: async () => [] }'
      if (id.endsWith('/src/apis/auth_api.js')) return 'export const authApi = { getUsersPage: async () => ({ items: [], total: 0 }) }'
      if (id.endsWith('.vue') && !id.endsWith('/FileTable.vue') && !id.endsWith('/KnowledgeBaseInfoView.vue')) return '<script>export default {}</script>'
    }
  }] })
  tableComponent = (await fixtureServer.ssrLoadModule('/src/modules/knowledge/ui/FileTable.vue')).default
  pageComponent = (await fixtureServer.ssrLoadModule('/src/pages/KnowledgeBaseInfoView.vue')).default
})
after(async () => { await fixtureServer.close() })
afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  delete globalThis.__fileEntry
  delete globalThis.document
})

/** 通过真实组件与 API 边界观察文件操作和持久任务回执。 */
function fixture(overrides = {}) {
  const requests = []
  const registrations = []
  const feedback = []
  const refreshed = []
  const events = []
  const props = reactive({ readonly: false })
  const store = reactive({
    kbId: 'kb-a', knowledgeBase: { kb_type: 'milvus' }, state: { lock: false },
    fileBrowser: {}, documentFiles: [], folderBreadcrumbs: [], selectedRowKeys: [],
    getKnowledgeBaseInfo: async () => { refreshed.push(true) }, loadDocumentFiles: async () => {},
    resetFileBrowser() {}, closeFileDetail() {}, stopAutoRefresh() {}, startAutoRefresh() {}
  })
  const userStore = reactive({ token: 'session-a' })
  const documentApi = {
    getDocumentInfo: async () => ({ meta: { processing_params: { ocr_engine: 'existing-ocr' } } }),
    reparseDocument: async (...args) => { requests.push(args); return { job_id: 'reparse-job' } },
    ...overrides.documentApi
  }
  if (overrides.loadRecordProcessingParams) documentApi.getDocumentInfo = async (...args) => ({ meta: { processing_params: await overrides.loadRecordProcessingParams(...args) } })
  const graphBuildApi = { clearFile: async () => ({ job_id: 'job-a' }), ...overrides.graphBuildApi }
  globalThis.__fileEntry = { store, userStore, documentApi, graphBuildApi,
    jobsStore: { createJobRegistration: (onSettled) => (job) => registrations.push({ job, onSettled }) },
    message: { success: (text) => feedback.push(['success', text]), error: (text) => feedback.push(['error', text]), warning: (text) => feedback.push(['warning', text]) }
  }
  let state
  const app = renderer.createApp({ setup() { state = tableComponent.setup(props, { expose() {}, emit: (...args) => events.push(args) }); return () => h('div') } })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(node())
  mounted.push(app)
  return { ...state, readonly: toRef(props, 'readonly'), lock: toRef(store.state, 'lock'), store, userStore, requests, registrations, feedback, refreshed, events }
}

test('文件菜单提供查看图谱，点击关闭菜单并传递当前文件', () => {
  const source = readFileSync(new URL(tablePath, import.meta.url), 'utf8')
  assert.match(source, /<template v-else>\s*<a-button[^>]*@click="viewFileGraph\(row\)"[^>]*>[\s\S]*?查看图谱/)
  const action = fixture()
  action.popoverVisibleMap.value['file-a'] = true
  const file = { file_id: 'file-a', file_name: '合同.pdf' }
  action.viewFileGraph(file)
  assert.equal(action.popoverVisibleMap.value['file-a'], false)
  assert.deepEqual(action.events, [['view-graph', file]])
})

test('查看图谱直接进入所选文件并保留其他路由参数', () => {
  fixture()
  const pushes = []
  let page
  const app = renderer.createApp({ setup() { page = pageComponent.setup({}, { expose() {}, emit() {} }); return () => h('div') } })
  app.provide(ssrContextKey, { modules: new Set() })
  app.provide(routerKey, { push: (target) => pushes.push(target), replace() {} })
  app.provide(routeLocationKey, { params: {}, query: { section: 'filetable', folder: 'folder-a' } })
  globalThis.document = { addEventListener() {}, removeEventListener() {} }
  app.mount(node())
  mounted.push(app)
  page.viewFileGraph({ file_id: 'file-a' })
  assert.deepEqual(pushes, [{ query: { section: 'graph', folder: 'folder-a', file_id: 'file-a' } }])
})

function clearGraphAction(overrides = {}) {
  const action = fixture(overrides)
  return { ...action, get registered() { return action.registrations.map((entry) => entry.job) } }
}

test('清除图谱仅允许 Milvus 中有分块的稳定文件，其他状态不打开确认框', () => {
  const action = clearGraphAction()
  const file = { file_id: 'file-a', filename: '合同.pdf', status: 'indexed', chunk_count: 2 }
  for (const status of ['done', 'indexed', 'parsed', 'error_indexing', 'failed']) {
    assert.equal(action.canClearFileGraph({ ...file, status }), true)
  }
  for (const status of ['uploaded', 'parsing', 'indexing', 'processing', 'waiting', 'unknown']) {
    action.handleClearFileGraph({ ...file, status })
    assert.equal(action.canClearFileGraph({ ...file, status }), false)
  }
  for (const chunkCount of [undefined, 0, -1]) {
    action.handleClearFileGraph({ ...file, chunk_count: chunkCount })
  }
  action.handleClearFileGraph({ ...file, is_folder: true })
  for (const state of [action.readonly, action.lock, action.documentReparseSubmitting]) {
    state.value = true
    action.handleClearFileGraph(file)
    state.value = false
  }
  action.clearingGraphFileId.value = 'file-b'
  action.handleClearFileGraph(file)
  action.clearingGraphFileId.value = null
  action.store.knowledgeBase.kb_type = 'other'
  action.handleClearFileGraph(file)
  assert.equal(action.fileGraphClearTarget.value, null)
})

test('清除图谱先确认保留内容，提交期间阻止重复操作并跟踪持久作业', async () => {
  const requests = []
  let finishRequest
  const action = clearGraphAction({
    graphBuildApi: {
      clearFile: (kbId, fileId) => {
        requests.push([kbId, fileId])
        return new Promise((resolve) => { finishRequest = resolve })
      }
    }
  })
  const file = { file_id: 'file-a', filename: '合同.pdf', status: 'indexed', chunk_count: 2 }
  action.handleClearFileGraph(file)
  assert.deepEqual(requests, [])
  const source = readFileSync(new URL(tablePath, import.meta.url), 'utf8')
  assert.match(source, /保留原件、Markdown、分块和普通检索/)
  assert.match(source, /ok-text="清除图谱"/)
  assert.match(source, /:ok-button-props="\{ danger: true \}"/)
  assert.deepEqual(action.fileGraphClearTarget.value, { row: file, kbId: 'kb-a' })
  const submitting = action.submitClearFileGraph()
  assert.equal(action.clearingGraphFileId.value, 'file-a')
  await action.submitClearFileGraph()
  assert.deepEqual(requests, [['kb-a', 'file-a']])
  finishRequest({ job_id: 'job-a' })
  await submitting
  assert.equal(action.clearingGraphFileId.value, null)
  assert.equal(action.fileGraphClearTarget.value, null)
  assert.deepEqual(action.registered, [{ job_id: 'job-a' }])
  assert.match(action.feedback[0][1], /任务已提交/)
})

test('清除请求失败保留确认框重试语义并恢复提交状态', async () => {
  const action = clearGraphAction({
    graphBuildApi: { clearFile: async () => { throw new Error('当前有图谱任务正在运行') } }
  })
  const file = { file_id: 'file-a', status: 'indexed', chunk_count: 2 }
  action.handleClearFileGraph(file)
  await action.submitClearFileGraph()
  assert.equal(action.clearingGraphFileId.value, null)
  assert.deepEqual(action.fileGraphClearTarget.value, { row: file, kbId: 'kb-a' })
  assert.deepEqual(action.registered, [])
  assert.deepEqual(action.feedback, [['error', '当前有图谱任务正在运行']])
})

test('确认时切换知识库或变为只读不提交原文件清除', async () => {
  const action = clearGraphAction({
    graphBuildApi: { clearFile: async () => { assert.fail('不得清除') } }
  })
  await action.submitClearFileGraph()
  const file = { file_id: 'file-a', status: 'indexed', chunk_count: 2 }
  action.handleClearFileGraph(file)
  action.store.kbId = 'kb-b'
  await action.submitClearFileGraph()
  action.store.kbId = 'kb-a'
  action.readonly.value = true
  await action.submitClearFileGraph()
  assert.deepEqual(action.registered, [])
})

function documentReparseAction(overrides = {}) {
  return fixture(overrides)
}

const reparsableFile = {
  file_id: 'file-a', filename: '合同.pdf', status: 'indexed',
  has_parsed_markdown: true, has_original_file: true
}

test('文件菜单仅提供普通重新解析，并说明完成后需要重新入库和构图', () => {
  const source = readFileSync(new URL(tablePath, import.meta.url), 'utf8')
  assert.doesNotMatch(source, /清除入库数据|清除解析结果|documentResetActions/)
  const menuButton = source.match(/<a-button\b[^>]*@click="handleReparseDocument\(row\)"[^>]*>/)?.[0]
  const confirmButton = source.match(/<a-button\s+key="submit"[^>]*@click="handleParseConfigConfirm"[^>]*>/)?.[0]
  assert.ok(menuButton)
  assert.ok(confirmButton)
  assert.doesNotMatch(menuButton, /danger/)
  assert.doesNotMatch(confirmButton, /danger/)
  assert.match(source, /完成后为待入库，需要重新入库并构建图谱/)
})

test('有原件的稳定与失败文件可重解析，处理中、未知和缺原件禁用', async () => {
  const action = documentReparseAction()
  for (const status of ['uploaded', 'parsed', 'done', 'indexed', 'error_parsing', 'error_indexing', 'failed']) {
    assert.equal(action.canReparseDocument({ ...reparsableFile, status }), true)
  }
  for (const status of ['processing', 'waiting', 'parsing', 'indexing', 'unknown']) {
    await action.handleReparseDocument({ ...reparsableFile, status })
    assert.equal(action.documentReparseTarget.value, null)
  }
  assert.equal(action.canReparseDocument({ ...reparsableFile, is_folder: true }), false)
  assert.equal(action.canReparseDocument({ ...reparsableFile, has_original_file: undefined }), false)
  assert.equal(action.canReparseDocument({ ...reparsableFile, has_original_file: false }), false)
  for (const blocked of [action.readonly, action.lock, action.documentReparseSubmitting]) {
    blocked.value = true
    assert.equal(action.canReparseDocument(reparsableFile), false)
    blocked.value = false
  }
  action.clearingGraphFileId.value = 'file-b'
  assert.equal(action.canReparseDocument(reparsableFile), false)
  action.clearingGraphFileId.value = null
  action.store.knowledgeBase.kb_type = 'other'
  assert.equal(action.canReparseDocument(reparsableFile), false)
  action.store.knowledgeBase.kb_type = 'milvus'
  assert.equal(action.canReparseDocument({ ...reparsableFile, chunk_count: 0, has_parsed_markdown: false }), true)
})

test('重新解析沿用参数且只提交后台任务，终态回读后端状态', async () => {
  const action = documentReparseAction()
  await action.handleReparseDocument(reparsableFile)
  assert.deepEqual(action.requests, [])
  assert.equal(action.parseConfigModalVisible.value, true)
  assert.deepEqual(action.parseParams.value, { ocr_engine: 'existing-ocr' })
  action.parseParams.value = { ocr_engine: 'selected-ocr' }
  assert.match(action.parseConfigModalTitle.value, /重新解析.*合同.pdf/)
  await action.handleParseConfigConfirm()
  assert.deepEqual(action.requests, [['kb-a', 'file-a', { ocr_engine: 'selected-ocr' }]])
  assert.equal(action.documentReparseSubmitting.value, false)
  assert.equal(action.documentReparseTarget.value, null)
  assert.equal(action.parseConfigModalVisible.value, false)
  assert.equal(reparsableFile.status, 'indexed', '提交回执不能乐观改写文件状态')
  assert.deepEqual(action.registrations[0].job, { job_id: 'reparse-job' })
  assert.equal(action.refreshed.length, 1)
  action.registrations[0].onSettled()
  assert.equal(action.refreshed.length, 2)
  action.store.kbId = 'kb-b'
  action.registrations[0].onSettled()
  assert.equal(action.refreshed.length, 2)
  action.store.kbId = 'kb-a'
  action.userStore.token = 'session-b'
  action.registrations[0].onSettled()
  assert.equal(action.refreshed.length, 2)
})

test('重新解析提交失败保留参数，可重试且不登记作业', async () => {
  const action = documentReparseAction({
    documentApi: { reparseDocument: async () => { throw new Error('当前有处理任务正在运行') } }
  })
  await action.handleReparseDocument(reparsableFile)
  await action.submitReparseDocument({ ocr_engine: 'selected-ocr' })
  assert.equal(action.documentReparseSubmitting.value, false)
  assert.equal(action.documentReparseTarget.value.row.file_id, 'file-a')
  assert.equal(action.parseConfigModalVisible.value, true)
  assert.deepEqual(action.registrations, [])
  assert.deepEqual(action.refreshed, [])
  assert.deepEqual(action.feedback, [['error', '当前有处理任务正在运行']])
})

test('确认重解析前切库、切账号、只读或重复提交都不产生额外请求', async () => {
  let finishRequest
  const action = documentReparseAction({
    documentApi: { reparseDocument: () => new Promise((resolve) => { finishRequest = resolve }) }
  })
  await action.submitReparseDocument()
  await action.handleReparseDocument(reparsableFile)
  action.store.kbId = 'kb-b'
  await action.submitReparseDocument()
  assert.equal(finishRequest, undefined)
  action.store.kbId = 'kb-a'
  action.userStore.token = 'session-b'
  await action.submitReparseDocument()
  assert.equal(finishRequest, undefined)
  action.userStore.token = 'session-a'
  action.readonly.value = true
  await action.submitReparseDocument()
  assert.equal(finishRequest, undefined)
  action.readonly.value = false
  const pending = action.submitReparseDocument()
  const firstRequest = finishRequest
  await action.submitReparseDocument()
  assert.equal(finishRequest, firstRequest)
  finishRequest({ job_id: 'reparse-job' })
  await pending
  assert.equal(action.registrations.length, 1)
})

test('重新解析参数读取失败可见，切库后的旧参数不打开弹窗', async () => {
  const failed = documentReparseAction({ loadRecordProcessingParams: async () => { throw new Error('读取参数失败') } })
  await failed.handleReparseDocument(reparsableFile)
  assert.equal(failed.documentReparseTarget.value, null)
  assert.deepEqual(failed.feedback, [['error', '读取参数失败']])
  let releaseParams
  const switched = documentReparseAction({ loadRecordProcessingParams: () => new Promise((resolve) => { releaseParams = resolve }) })
  const opening = switched.handleReparseDocument(reparsableFile)
  switched.store.kbId = 'kb-b'
  releaseParams({ ocr_engine: 'old-ocr' })
  await opening
  assert.equal(switched.parseConfigModalVisible.value, false)
})

test('重新解析从真实详情响应的 meta 读取已有 OCR 参数', async () => {
  const requests = []
  const action = documentReparseAction({ documentApi: {
    getDocumentInfo: async (...args) => {
      requests.push(args)
      return { meta: { processing_params: { ocr_engine: 'persisted-ocr' } }, content: '原正文' }
    }
  } })
  await action.handleReparseDocument(reparsableFile)
  assert.deepEqual(action.parseParams.value, { ocr_engine: 'persisted-ocr' })
  assert.deepEqual(requests, [['kb-a', 'file-a']])
})

test('文件图谱和重新解析 API 使用管理端 POST 并保留作业回执', async () => {
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  const originalFetch = globalThis.fetch
  const originalStorage = globalThis.localStorage
  globalThis.localStorage = { getItem: (key) => key === 'user_token' ? 'test-token' : null }
  const requests = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method, ...(options.body ? { body: JSON.parse(options.body) } : {}) })
    return new Response(JSON.stringify({ status: 'queued', job_id: 'job-a' }), {
      status: 200,
      headers: { 'content-type': 'application/json' }
    })
  }
  try {
    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    useUserStore().userRole = 'superadmin'
    const { graphBuildApi, documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    assert.deepEqual(await graphBuildApi.clearFile('kb-a', 'file/a'), {
      status: 'queued', job_id: 'job-a'
    })
    assert.equal(documentApi.clearDocumentIndex, undefined)
    assert.equal(documentApi.clearDocumentParse, undefined)
    await documentApi.reparseDocument('kb-a', 'file/a', { ocr_engine: 'existing-ocr' })
    await documentApi.getDocumentMarkdown('kb-a', 'file/a')
    await documentApi.saveDocumentMarkdown('kb-a', 'file/a', 'opaque-revision', '')
    assert.deepEqual(requests, [
      { url: '/api/knowledge/knowledge-bases/kb-a/graph-build/files/file%2Fa/clear', method: 'POST', body: {} },
      { url: '/api/knowledge/knowledge-bases/kb-a/documents/file%2Fa/reparse', method: 'POST', body: { params: { ocr_engine: 'existing-ocr' } } },
      { url: '/api/knowledge/knowledge-bases/kb-a/documents/file%2Fa/markdown', method: 'GET' },
      { url: '/api/knowledge/knowledge-bases/kb-a/documents/file%2Fa/markdown', method: 'POST', body: { expected_revision: 'opaque-revision', content: '' } }
    ])
  } finally {
    globalThis.fetch = originalFetch
    globalThis.localStorage = originalStorage
    await server.close()
  }
})

test('Markdown 编辑独立于阅读物料准入，稳定失败态无原件也能人工编辑', () => {
  const action = fixture()
  action.popoverVisibleMap.value['empty-file'] = true
  const row = { file_id: 'empty-file', status: 'error_parsing', has_original_file: false, has_parsed_markdown: false }
  action.editFileMarkdown(row)
  assert.deepEqual(action.events, [['edit-markdown', row]])
  assert.equal(action.popoverVisibleMap.value['empty-file'], false)
  for (const status of ['uploaded', 'parsed', 'indexed', 'error_indexing', 'failed']) assert.equal(action.canEditFileMarkdown({ ...row, status }), true)
  for (const status of ['parsing', 'indexing', 'processing', 'unknown']) assert.equal(action.canEditFileMarkdown({ ...row, status }), false)
  action.readonly.value = true
  assert.equal(action.canEditFileMarkdown(row), false)
  assert.equal(action.canEditFileMarkdown({ ...row, is_folder: true }), false)
})
