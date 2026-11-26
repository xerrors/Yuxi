import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createRenderer, effectScope, h, KeepAlive, nextTick, reactive, ref, ssrContextKey } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

const artifactPath = '/home/user/workspace/projects/demo/方案.html'
const artifactPreview = { path: artifactPath, name: '方案.html', workdir: false, key: `thread-a:${artifactPath}`, threadId: 'thread-a' }
let server, component, threadApi, workspaceApi
const oldWindow = globalThis.window, oldDocument = globalThis.document, oldStorage = globalThis.localStorage
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  workspaceApi = {}
  globalThis.__artifactWorkspaceApi = workspaceApi
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{ name: 'artifact-workspace-api', enforce: 'pre', load(id) {
      if (id.endsWith('/src/apis/workspace_api.js')) return `
        export const getWorkspaceTree = (...args) => globalThis.__artifactWorkspaceApi.getWorkspaceTree(...args)
        export const getWorkspaceFileContent = (...args) => globalThis.__artifactWorkspaceApi.getWorkspaceFileContent(...args)
        export const downloadWorkspaceFile = () => {}
        export const searchWorkspaceFiles = () => {}`
    } }]
  })
  ;({ threadApi } = await server.ssrLoadModule('/src/apis/agent_api.js'))
  ;({ default: component } = await server.ssrLoadModule('/src/modules/session/ui/workspace/AgentPanel.vue'))
  globalThis.window = { URL, addEventListener() {}, removeEventListener() {} }
  globalThis.document = { body: { style: {} }, addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', compatMode: 'CSS1Compat' }
})
after(async () => {
  await server?.close()
  delete globalThis.__artifactWorkspaceApi
  if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow
  if (oldDocument === undefined) delete globalThis.document; else globalThis.document = oldDocument
  if (oldStorage === undefined) delete globalThis.localStorage; else globalThis.localStorage = oldStorage
})
const renderer = createRenderer({
  createElement: () => ({ children: [] }), createText: () => ({}), createComment: () => ({}),
  insert(child, parent) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); child.parent = parent; parent.children.push(child) },
  remove(child) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1) },
  setText() {}, setElementText() {}, parentNode: (node) => node.parent, nextSibling: () => null, patchProp() {}
})

/** 执行真实面板 setup，以文件响应和终态通知驱动缓存生命周期。 */
async function setupPanel(t) {
  const hooks = {}
  let content = '<h1>预算20万元</h1>'
  const requests = []
  const props = reactive({
    threadId: 'thread-a',
    visible: true,
    activeSectionKey: `file:${artifactPath}`,
    sections: [{ key: `file:${artifactPath}`, type: 'file' }],
    activePreview: artifactPreview,
    previewCache: new Map(),
    filesystemVisible: false,
    filesystemPollingActive: false,
    filesystemRefreshVersions: new Map()
  })
  const oldPreview = threadApi.previewThreadArtifact
  const oldUrl = threadApi.getThreadArtifactUrl
  const oldTree = workspaceApi.getWorkspaceTree
  const oldContent = workspaceApi.getWorkspaceFileContent
  threadApi.getThreadArtifactUrl = () => '/artifact'
  threadApi.previewThreadArtifact = async (threadId, path) => {
    requests.push({ threadId, path })
    return new Response(JSON.stringify({ content, preview_type: 'html', supported: true }), {
      headers: { 'content-type': 'application/json' }
    })
  }
  workspaceApi.getWorkspaceTree = async () => ({ entries: [] })
  workspaceApi.getWorkspaceFileContent = async () => new Response(
    JSON.stringify({ content: '个人文件', preview_type: 'text', supported: true }),
    { headers: { 'content-type': 'application/json' } }
  )
  let view
  const active = ref(true)
  const Panel = { setup() {
    view = component.setup(props, { expose() {}, emit() {} })
    return () => null
  } }
  const app = renderer.createApp({ setup: () => () => h(KeepAlive, null, () => active.value ? h(Panel) : null) })
  app.provide(ssrContextKey, {})
  app.mount({ children: [] })
  hooks.deactivated = async () => { active.value = false; await nextTick() }
  hooks.activated = async () => { active.value = true; await nextTick() }
  const settle = async () => {
    await nextTick()
    await Promise.all(
      [...props.previewCache.values()]
        .filter((entry) => entry.status === 'loading')
        .map((entry) => entry.promise)
    )
    await nextTick()
  }
  t.after(() => {
    app.unmount()
    threadApi.previewThreadArtifact = oldPreview
    threadApi.getThreadArtifactUrl = oldUrl
    workspaceApi.getWorkspaceTree = oldTree
    workspaceApi.getWorkspaceFileContent = oldContent
  })
  await settle()
  return {
    props,
    view,
    hooks,
    requests,
    settle,
    refresh(threadId = 'thread-a') {
      const versions = props.filesystemRefreshVersions
      props.filesystemRefreshVersions = new Map(versions).set(threadId, (versions.get(threadId) || 0) + 1)
    },
    update: (value) => {
      content = value
    }
  }
}

test('同名交付物在终态通知后更新，默认个人空间树不阻断活动产物刷新', async (t) => {
  const panel = await setupPanel(t)
  assert.match(panel.view.currentFile.value.content, /20万元/)
  assert.equal(panel.requests.length, 1, '同时触发的首次预览应共享同一次读取')
  panel.props.previewCache.set('thread-b:/other', { status: 'ready', file: { content: 'other' } })
  panel.props.previewCache.set('thread-a:/inactive', { status: 'ready', file: { content: 'old' } })
  panel.update('<h1>预算08万元</h1>')
  panel.refresh()
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /08万元/)
  assert.equal(panel.props.previewCache.has('thread-a:/inactive'), false)
  assert.equal(panel.props.previewCache.has('thread-b:/other'), true)
})

test('产物预览中的手动刷新读取当前产物，不只刷新个人空间树', async (t) => {
  const panel = await setupPanel(t)
  panel.update('<h1>手动修订</h1>')
  await panel.view.emitRefresh()
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /手动修订/)
})

test('再次点击同名交付卡片使未知元数据缓存失效并触发面板重读', async (t) => {
  const panel = await setupPanel(t)
  panel.update('<h1>最新卡片</h1>')
  const { useSessionPanel } = await server.ssrLoadModule('/src/modules/session/model/useSessionPanel.js')
  const pinia = createPinia()
  setActivePinia(pinia)
  const scope = effectScope()
  const state = scope.run(() => useSessionPanel({
    threadId: ref('thread-a'), statePanelOpen: ref(false), containerWidth: ref(1280)
  }))
  t.after(() => { scope.stop(); disposePinia(pinia) })
  state.openPanelPreview({ path: artifactPath, name: '方案.html' })
  for (const [key, value] of panel.props.previewCache) state.agentPanelPreviewCache.set(key, value)
  panel.props.previewCache = state.agentPanelPreviewCache
  state.openPanelPreview({ path: artifactPath, name: '方案.html' })
  panel.props.activePreview = state.activeAgentPanelPreview.value
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /最新卡片/)
})

test('KeepAlive 停用时不读取文件，返回页面时活动产物重新读取', async (t) => {
  const panel = await setupPanel(t)
  await panel.hooks.deactivated()
  panel.update('<h1>后台修订</h1>')
  panel.refresh()
  await panel.settle()
  assert.equal(panel.requests.length, 1)
  await panel.hooks.activated()
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /后台修订/)
})

test('关闭面板期间完成的同名修改在重开时可见，无需 KeepAlive 激活', async (t) => {
  const panel = await setupPanel(t)
  panel.props.visible = false
  await panel.settle()
  panel.update('<h1>关闭期间修订</h1>')
  panel.refresh()
  await panel.settle()
  assert.equal(panel.requests.length, 1)
  panel.props.visible = true
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /关闭期间修订/)
})

test('查看协作成员期间完成的修改在切回产物标签时可见', async (t) => {
  const panel = await setupPanel(t)
  panel.props.sections.push({ key: 'session:child', type: 'session' })
  panel.props.activeSectionKey = 'session:child'
  panel.props.activePreview = null
  await panel.settle()
  panel.update('<h1>协作期间修订</h1>')
  panel.refresh()
  await panel.settle()
  panel.props.activeSectionKey = `file:${artifactPath}`
  panel.props.activePreview = artifactPreview
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /协作期间修订/)
})

test('终态在个人文件标签发生时也使当前线程的非活动产物失效', async (t) => {
  const panel = await setupPanel(t)
  panel.props.sections.push({ key: 'file:/personal.md', type: 'file' })
  panel.props.activeSectionKey = 'file:/personal.md'
  panel.props.activePreview = { path: '/personal.md', name: 'personal.md', workspace: true, key: 'workspace:/personal.md' }
  await panel.settle()
  panel.update('<h1>其它标签期间修订</h1>')
  panel.refresh()
  await panel.settle()
  panel.props.activeSectionKey = `file:${artifactPath}`
  panel.props.activePreview = artifactPreview
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /其它标签期间修订/)
})

test('成员产物与根会话同路径时，按成员 Thread 读取且保留根预览', async (t) => {
  const panel = await setupPanel(t)
  panel.update('<h1>成员版本</h1>')
  panel.props.activePreview = { ...artifactPreview, key: `thread-child:${artifactPath}`, threadId: 'thread-child' }
  await panel.settle()
  assert.deepEqual(panel.requests, [
    { threadId: 'thread-a', path: artifactPath },
    { threadId: 'thread-child', path: artifactPath }
  ])
  assert.match(panel.view.currentFile.value.content, /成员版本/)
  panel.update('<h1>成员终态</h1>')
  panel.refresh('thread-child')
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /成员终态/)
  panel.props.activePreview = artifactPreview
  await panel.settle()
  assert.match(panel.view.currentFile.value.content, /20万元/)
})
