import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers'
import test, { before, after, afterEach } from 'node:test'
import { createRenderer, reactive, ref, ssrContextKey } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { matchedRouteKey } from 'vue-router'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

const detailPath = '../../src/modules/knowledge/ui/FileDetailModal.vue'
let server, component, documentApi, jobsApi
const cleanups = []
const previousStorage = globalThis.localStorage
const previousWindow = globalThis.window
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  ;({ documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js'))
  ;({ backgroundJobsApi: jobsApi } = await server.ssrLoadModule('/src/apis/background_jobs.js'))
  ;({ default: component } = await server.ssrLoadModule('/src/modules/knowledge/ui/FileDetailModal.vue'))
  globalThis.window = { URL, addEventListener() {}, removeEventListener() {} }
})
afterEach(async () => { while (cleanups.length) await cleanups.pop()() })
after(async () => {
  await server?.close()
  if (previousWindow === undefined) delete globalThis.window
  else globalThis.window = previousWindow
  if (previousStorage === undefined) delete globalThis.localStorage
  else globalThis.localStorage = previousStorage
})
const renderer = createRenderer({
  createElement: () => ({ children: [] }), createText: () => ({}), createComment: () => ({}),
  insert: (child, parent) => parent.children.push(child), remove() {}, setText() {}, setElementText() {},
  parentNode: () => null, nextSibling: () => null, patchProp() {}
})

/** 使用真实文件详情和作业 Store，隔离 HTTP 边界。 */
async function editorLogic(overrides = {}) {
  const events = [], feedback = [], registrations = []
  const props = reactive({ editable: true, open: true, kbId: 'kb-a', fileId: 'file-a', ...overrides.props })
  const originalApi = { getDocumentBasicInfo: documentApi.getDocumentBasicInfo,
    getDocumentMarkdown: documentApi.getDocumentMarkdown, getDocumentContent: documentApi.getDocumentContent, saveDocumentMarkdown: documentApi.saveDocumentMarkdown }
  const originalJobDetail = jobsApi.fetchJobDetail
  const oldSuccess = message.success
  message.success = (text) => feedback.push(text)
  documentApi.getDocumentContent = async () => ({ content: '', lines: [] })
  documentApi.getDocumentBasicInfo = async () => ({ file_id: 'file-a', status: 'parsed' })
  documentApi.getDocumentMarkdown = overrides.documentApi?.getDocumentMarkdown || (async () => ({ content: '# 原文', revision: 'rev-a', has_parsed_markdown: true }))
  const save = overrides.documentApi?.saveDocumentMarkdown || (async () => ({ job_id: 'job-a', status: 'queued' }))
  documentApi.saveDocumentMarkdown = async (...args) => {
    const receipt = await save(...args)
    if (receipt?.job_id) registrations.push(receipt)
    return receipt
  }
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const { useBackgroundJobsStore } = await server.ssrLoadModule('/src/modules/background-jobs/model/jobs.js')
  const userStore = useUserStore(pinia)
  userStore.userRole = 'admin'
  userStore.token = 'account-a'
  const jobsStore = useBackgroundJobsStore(pinia)
  jobsApi.fetchJobDetail = async (id) => {
    return { job: jobsStore.jobs.findLast((job) => job.id === id) || { id, status: 'pending' } }
  }
  let editor
  const app = renderer.createApp({ setup() {
    editor = component.setup(props, { expose() {}, emit: (...event) => {
      events.push(event)
      if (event[0] === 'update:open') props.open = event[1]
      if (event[0] === 'update:editRequested') props.editRequested = event[1]
    } })
    return () => null
  } })
  app.use(pinia)
  app.provide(ssrContextKey, {})
  app.provide(matchedRouteKey, ref({ leaveGuards: new Set(), updateGuards: new Set() }))
  app.mount({ children: [] })
  await new Promise((resolve) => setImmediate(resolve))
  editor.file.value = overrides.file?.value || { file_id: 'file-a', status: 'parsed' }
  cleanups.push(() => {
    app.unmount()
    disposePinia(pinia)
    Object.assign(documentApi, originalApi)
    jobsApi.fetchJobDetail = originalJobDetail
    message.success = oldSuccess
  })
  return { ...editor, props, userStore, jobsStore, documentApi, events, feedback, registrations,
    settle: async () => jobsStore.refreshJob('job-a') }
}

test('编辑仅管理入口启用，所有稳定状态无需已有物料，运行态拒绝', async () => {
  const action = await editorLogic()
  for (const status of ['uploaded', 'parsed', 'done', 'indexed', 'error_parsing', 'error_indexing', 'failed']) {
    action.file.value = { status }
    assert.equal(action.canEditMarkdown.value, true, status)
  }
  for (const status of ['parsing', 'indexing', 'processing', 'waiting', 'unknown']) {
    action.file.value = { status }
    assert.equal(action.canEditMarkdown.value, false, status)
  }
  action.file.value = { status: 'parsed', is_folder: true }
  assert.equal(action.canEditMarkdown.value, false)
  action.file.value = { status: 'parsed' }
  action.props.editable = false
  // props 在实际组件中响应式，这里重新装配只读调用点。
  assert.equal((await editorLogic({ props: { editable: false } })).canEditMarkdown.value, false)
})

test('严格读取成功才能编辑，失败与不完整响应不使用分块或空串', async () => {
  const action = await editorLogic()
  let started = 0
  action.markdownPreview.value = { startEditing: () => { started++ } }
  await action.startMarkdownEdit()
  assert.equal(action.markdownEditor.value.draft, '# 原文')
  assert.equal(action.markdownEditor.value.revision, 'rev-a')
  assert.equal(started, 1)
  for (const getDocumentMarkdown of [async () => { throw new Error('对象读取失败') }, async () => ({ revision: 'r', has_parsed_markdown: true })]) {
    const failed = await editorLogic({ documentApi: { getDocumentMarkdown } })
    await failed.startMarkdownEdit()
    assert.equal(failed.markdownEditor.value.loaded, false)
    assert.equal(failed.canSaveMarkdown.value, false)
    assert.ok(failed.markdownEditor.value.error)
  }
})

test('无解析指针允许显式空串保存，进入和取消均无写入', async () => {
  const requests = []
  const action = await editorLogic({ documentApi: {
    getDocumentMarkdown: async () => ({ content: '', revision: 'empty-rev', has_parsed_markdown: false }),
    saveDocumentMarkdown: async (...args) => { requests.push(args); return { job_id: 'job-a' } }
  } })
  await action.startMarkdownEdit()
  assert.equal(action.canSaveMarkdown.value, true)
  action.cancelMarkdownEdit()
  assert.deepEqual(requests, [])
  await action.startMarkdownEdit()
  await action.submitMarkdownSave()
  assert.deepEqual(requests, [['kb-a', 'file-a', 'empty-rev', '']])
  assert.equal(action.markdownEditor.value.saving, true)
  assert.equal(action.visible.value, true)
  assert.deepEqual(action.events, [])
  action.jobsStore.jobs.push({ id: 'job-a', status: 'success' })
  await action.settle()
  assert.equal(action.visible.value, false)
  assert.deepEqual(action.events, [['saved'], ['update:open', false]])
  assert.equal(action.feedback.length, 1)
})

test('已入库保存先确认，入队后不关闭且禁止重复提交，任务失败保留草稿', async () => {
  const requests = []
  const action = await editorLogic({ file: ref({ status: 'indexed', active_generation: 7 }), documentApi: {
    getDocumentMarkdown: async () => ({ content: 'old', revision: 'rev-indexed', has_parsed_markdown: true }),
    saveDocumentMarkdown: async (...args) => { requests.push(args); return { job_id: 'job-a' } }
  } })
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = 'new'
  action.requestMarkdownSave()
  assert.equal(action.markdownSaveConfirmOpen.value, true)
  assert.deepEqual(requests, [])
  await action.submitMarkdownSave()
  await action.submitMarkdownSave()
  action.cancelMarkdownEdit()
  assert.equal(requests.length, 1)
  assert.equal(action.markdownEditor.value.active, true)
  assert.equal(action.visible.value, true)
  action.jobsStore.jobs.push({ id: 'job-a', status: 'failed', error: '写入失败，请重试' })
  await action.settle()
  assert.equal(action.markdownEditor.value.draft, 'new')
  assert.match(action.markdownEditor.value.error, /保存结果需确认.*写入失败/)
  assert.equal(action.canSaveMarkdown.value, false)
  assert.deepEqual(action.events, [])
})

test('默认代次不是下游证据，uploaded 和 parsed 空稿直接保存，入库失败仍确认', async () => {
  for (const status of ['uploaded', 'parsed']) {
    const action = await editorLogic({ file: ref({ status, active_generation: 1, chunk_count: 0 }), documentApi: {
      getDocumentMarkdown: async () => ({ content: '', revision: 'empty-gen-one', has_parsed_markdown: false }),
      saveDocumentMarkdown: async () => ({ job_id: 'job-a' })
    } })
    await action.startMarkdownEdit()
    action.requestMarkdownSave()
    assert.equal(action.markdownSaveConfirmOpen.value, false, status)
    assert.equal(action.markdownEditor.value.saving, true, status)
  }
  for (const status of ['indexed', 'done', 'error_indexing']) {
    const action = await editorLogic({ file: ref({ status, active_generation: 1, chunk_count: 0 }) })
    await action.startMarkdownEdit()
    action.markdownEditor.value.draft = '新稿'
    action.requestMarkdownSave()
    assert.equal(action.markdownSaveConfirmOpen.value, true, status)
    assert.deepEqual(action.registrations, [])
  }
})

test('409 保留修订与草稿并阻止盲目重试，切换账号后旧回执无效果', async () => {
  const action = await editorLogic({ documentApi: {
    getDocumentMarkdown: async () => ({ content: 'old', revision: 'rev-old', has_parsed_markdown: true }),
    saveDocumentMarkdown: async () => { throw Object.assign(new Error('修订已变更，请重新读取'), { status: 409 }) }
  } })
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = 'draft'
  await action.submitMarkdownSave()
  assert.equal(action.markdownEditor.value.draft, 'draft')
  assert.equal(action.markdownEditor.value.revision, 'rev-old')
  assert.match(action.markdownEditor.value.error, /草稿已保留/)
  assert.equal(action.canSaveMarkdown.value, false)
  const switched = await editorLogic()
  await switched.startMarkdownEdit()
  switched.markdownEditor.value.draft = 'new'
  await switched.submitMarkdownSave()
  switched.userStore.token = 'account-b'
  switched.jobsStore.jobs.push({ id: 'job-a', status: 'success' })
  await switched.settle()
  assert.deepEqual(switched.events, [])
  assert.equal(switched.visible.value, true)
})

test('共享编辑器显式取消事件和详情 opt-in，原有只读调用点不启用编辑', () => {
  const detail = readFileSync(new URL(detailPath, import.meta.url), 'utf8')
  const preview = readFileSync(new URL('../../src/modules/session/ui/workspace/AgentFilePreview.vue', import.meta.url), 'utf8')
  const page = readFileSync(new URL('../../src/pages/KnowledgeBaseInfoView.vue', import.meta.url), 'utf8')
  assert.match(detail, /editable: \{ type: Boolean, default: false \}/)
  assert.match(detail, /:show-edit-actions="false"/)
  assert.match(detail, /:content="markdownEditor.draft"/)
  assert.match(preview, /const cancelEdit[\s\S]*?emit\('cancel-edit'\)/)
  assert.match(page, /:editable="canManageKnowledgeBase && isMilvus"/)
  assert.match(page, /@saved="handleMarkdownSaved"/)
  assert.match(page, /v-model:edit-requested="markdownEditRequested"/)
  assert.match(detail, /保存将撤销此文件的分块、普通检索和图谱/)
  assert.doesNotMatch(detail.slice(detail.indexOf('<a-modal\n')), /danger/)
})


test('脏稿取消与关闭均先确认，继续编辑不修改草稿或创建请求', async () => {
  const action = await editorLogic()
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = '# 未保存草稿'
  action.cancelMarkdownEdit()
  assert.equal(action.markdownExitOpen.value, true)
  assert.equal(action.markdownEditor.value.active, true)
  assert.equal(action.markdownEditor.value.draft, '# 未保存草稿')
  action.markdownExitOpen.value = false
  action.requestMarkdownExit(true)
  assert.equal(action.visible.value, true)
  assert.deepEqual(action.registrations, [])
  action.confirmMarkdownExit()
  assert.equal(action.visible.value, false)
  assert.equal(action.markdownEditor.value.active, false)
})

test('保存结果未知仍能明确退出，迟到的失败回执不再改变已关闭界面', async () => {
  const action = await editorLogic()
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = '# 重要草稿'
  await action.submitMarkdownSave()
  action.cancelMarkdownEdit()
  assert.equal(action.markdownExitOpen.value, true)
  assert.equal(action.markdownEditor.value.saving, true)
  assert.equal(action.markdownEditor.value.draft, '# 重要草稿')
  action.confirmMarkdownExit()
  assert.equal(action.visible.value, false)
  action.jobsStore.jobs.push({ id: 'job-a', status: 'failed', error: '写入失败' })
  await action.settle()
  assert.deepEqual(action.events, [['update:open', false]])
})

test('冲突核对最新正文后才采纳新修订，旧草稿不变且可显式重试保存', async () => {
  const requests = []
  let latest = false
  const action = await editorLogic({ documentApi: {
    getDocumentMarkdown: async () => ({ content: latest ? '# 他人更新' : '# 原文', revision: latest ? 'rev-new' : 'rev-old', has_parsed_markdown: true }),
    saveDocumentMarkdown: async (...args) => {
      requests.push(args)
      if (args[2] === 'rev-old') throw Object.assign(new Error('冲突'), { status: 409 })
      return { job_id: 'job-a' }
    }
  } })
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = '# 我的草稿'
  await action.submitMarkdownSave()
  assert.equal(action.canSaveMarkdown.value, false)
  latest = true
  await action.readLatestMarkdown()
  assert.equal(action.latestMarkdown.value.open, true)
  assert.equal(action.latestMarkdown.value.data.content, '# 他人更新')
  assert.equal(action.markdownEditor.value.revision, 'rev-old')
  assert.equal(action.markdownEditor.value.draft, '# 我的草稿')
  await action.acceptLatestMarkdown()
  assert.equal(action.markdownEditor.value.revision, 'rev-new')
  assert.equal(action.markdownEditor.value.draft, '# 我的草稿')
  assert.equal(action.canSaveMarkdown.value, true)
  await action.submitMarkdownSave()
  assert.deepEqual(requests.at(-1), ['kb-a', 'file-a', 'rev-new', '# 我的草稿'])
})

test('最新正文读取失败或过期响应不会替换冲突基线与草稿', async () => {
  let resolveRead
  const action = await editorLogic()
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = '草稿'
  action.markdownEditor.value.conflict = true
  action.documentApi.getDocumentMarkdown = async () => { throw new Error('对象不可读') }
  await action.readLatestMarkdown()
  assert.equal(action.markdownEditor.value.revision, 'rev-a')
  assert.equal(action.markdownEditor.value.draft, '草稿')
  assert.equal(action.canSaveMarkdown.value, false)
  action.documentApi.getDocumentMarkdown = () => new Promise(resolve => { resolveRead = resolve })
  const pending = action.readLatestMarkdown()
  action.requestMarkdownExit(true)
  action.confirmMarkdownExit()
  resolveRead({ content: '迟到正文', revision: 'late', has_parsed_markdown: true })
  await pending
  assert.equal(action.latestMarkdown.value.open, false)
  assert.equal(action.markdownEditor.value.revision, 'rev-a')
})


test('正文已发布后任务取消：先回读确认相同正文，再刷新页面而不重复保存', async () => {
  const action = await editorLogic()
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = '# 已发布的草稿'
  await action.submitMarkdownSave()
  action.jobsStore.jobs.push({ id: 'job-a', status: 'cancelled', error: '已取消' })
  await action.settle()
  assert.equal(action.canSaveMarkdown.value, false)
  assert.equal(action.markdownEditor.value.draft, '# 已发布的草稿')
  assert.deepEqual(action.events, [])
  action.documentApi.getDocumentMarkdown = async () => ({ content: '# 已发布的草稿', revision: 'published', has_parsed_markdown: true })
  await action.readLatestMarkdown()
  assert.equal(action.latestMatchesDraft.value, true)
  assert.deepEqual(action.events, [])
  await action.acceptLatestMarkdown()
  assert.deepEqual(action.events, [['saved'], ['update:open', false]])
  assert.equal(action.feedback.at(-1), '文件已包含当前草稿')
  assert.equal(action.registrations.length, 1)
})


test('服务器已发布但响应丢失或缺回执：必须回读确认，不能重复提交', async () => {
  for (const failResponse of [() => { throw new TypeError('Failed to fetch') }, () => ({})]) {
    let content = '原文'
    let writes = 0
    const action = await editorLogic({ documentApi: {
      getDocumentMarkdown: async () => ({ content, revision: writes ? 'published' : 'original', has_parsed_markdown: true }),
      saveDocumentMarkdown: async (_kb, _file, _revision, draft) => { writes++; content = draft; return failResponse() }
    } })
    await action.startMarkdownEdit()
    action.markdownEditor.value.draft = '服务器实际已发布'
    await action.submitMarkdownSave()
    assert.equal(action.canSaveMarkdown.value, false)
    assert.match(action.markdownEditor.value.error, /保存结果需确认/)
    await action.submitMarkdownSave()
    assert.equal(writes, 1)
    assert.deepEqual(action.events, [])
    await action.readLatestMarkdown()
    assert.equal(action.latestMatchesDraft.value, true)
    await action.acceptLatestMarkdown()
    assert.equal(writes, 1)
    assert.deepEqual(action.events, [['saved'], ['update:open', false]])
  }
})


test('空串保存结果未知也保护退出与刷新，普通干净稿不拦截', async () => {
  const action = await editorLogic({ documentApi: {
    getDocumentMarkdown: async () => ({ content: '', revision: 'empty', has_parsed_markdown: false }),
    saveDocumentMarkdown: async () => { throw new TypeError('Failed to fetch') }
  } })
  await action.startMarkdownEdit()
  let prevented = 0
  const event = { preventDefault: () => { prevented++ } }
  action.protectMarkdownUnload(event)
  assert.equal(prevented, 0)
  await action.submitMarkdownSave()
  assert.equal(action.markdownDirty.value, false)
  assert.equal(action.markdownExitRequired.value, true)
  action.protectMarkdownUnload(event)
  assert.equal(prevented, 1)
  action.requestMarkdownExit(true)
  assert.equal(action.markdownExitOpen.value, true)
  assert.equal(action.visible.value, true)
})


test('同库切换视图不打断草稿，只有更换知识库需要导航确认', async () => {
  const action = await editorLogic()
  await action.startMarkdownEdit()
  action.markdownEditor.value.draft = '保留在当前文件的草稿'
  assert.equal(action.confirmMarkdownRouteUpdate({ params: { kbId: 'kb-a' }, query: { section: 'graph' } }, { params: { kbId: 'kb-a' }, query: { section: 'files' } }), true)
  assert.equal(action.markdownExitOpen.value, false)
  assert.equal(action.markdownEditor.value.draft, '保留在当前文件的草稿')
  const navigation = action.confirmMarkdownRouteUpdate({ params: { kbId: 'kb-b' } }, { params: { kbId: 'kb-a' } })
  assert.equal(action.markdownExitOpen.value, true)
  action.confirmMarkdownExit()
  assert.equal(await navigation, true)
})


test('编辑请求只消费一次，取消、关闭重开和换文件均恢复阅读', async () => {
  const action = await editorLogic({ props: { editRequested: true } })
  assert.equal(action.markdownEditor.value.active, true)
  assert.equal(action.props.editRequested, false)
  assert.deepEqual(action.events, [['update:editRequested', false]])
  action.cancelMarkdownEdit()
  assert.equal(action.markdownEditor.value.active, false)
  action.props.open = false
  await new Promise(resolve => setImmediate(resolve))
  action.props.open = true
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(action.markdownEditor.value.active, false)
  action.props.fileId = 'file-b'
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(action.markdownEditor.value.active, false)
})

test('分块编辑传递起始行，切换文件后迟到的全文读取不能恢复编辑', async () => {
  const action = await editorLogic()
  let line
  action.markdownPreview.value = { startEditing: value => { line = value } }
  await action.startMarkdownEdit(42)
  assert.equal(line, 42)
  action.cancelMarkdownEdit()
  let resolveRead
  action.documentApi.getDocumentMarkdown = () => new Promise(resolve => { resolveRead = resolve })
  const pending = action.startMarkdownEdit(7)
  action.props.fileId = 'file-b'
  await new Promise(resolve => setImmediate(resolve))
  resolveRead({ content: '迟到全文', revision: 'old', has_parsed_markdown: true })
  await pending
  assert.equal(action.markdownEditor.value.active, false)
  assert.notEqual(action.markdownEditor.value.draft, '迟到全文')
})
