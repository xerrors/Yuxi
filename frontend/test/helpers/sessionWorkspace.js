import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { createRenderer, h, ssrContextKey, toRaw } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createTestServer } from './createTestServer.js'

/** 装配真实的会话区域或根工作区，并控制历史读取边界。 */
export async function mountSessionWorkspace(t, readHistory = async () => {}, options = {}) {
  const globals = Object.fromEntries(['localStorage', 'document', 'window'].map((name) =>
    [name, Object.getOwnPropertyDescriptor(globalThis, name)]
  ))
  const previousPinia = getActivePinia()
  let server, pinia, app, mounted = false
  t.after(async () => {
    if (mounted) app.unmount()
    if (pinia) disposePinia(pinia)
    setActivePinia(previousPinia)
    try {
      await server?.close()
    } finally {
      for (const [name, descriptor] of Object.entries(globals)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else delete globalThis[name]
      }
    }
  })
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createTestServer({
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: options.stubChildren ? [{
      name: 'session-workspace-children', enforce: 'pre',
      resolveId(id, importer) {
        if (id === './ThreadTimeline.vue') return
        if (importer?.includes('/session/ui/ThreadTimeline.vue') && id.endsWith('.vue')) return '\0session-workspace-child'
        if (importer?.includes('/session/ui/SessionThreadView.vue') && id.endsWith('/AgentInputArea.vue')) return '\0session-workspace-input'
        if (importer?.match(/\/session\/ui\/Session(Workspace|ThreadView)\.vue/) && id.endsWith('.vue')) return '\0session-workspace-child'
      },
      load(id) {
        if (id === '\0session-workspace-input') return "import { h } from 'vue'; export default { setup(_, { attrs }) { return () => h('input', { ...attrs, 'data-session-input': '' }) } }"
        if (id === '\0session-workspace-child') return 'export default { render: () => null }'
      }
    }] : []
  })
  const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
  const originalFetch = globalThis.fetch
  t.mock.method(globalThis, 'fetch', (url, init) => {
    if (String(url).startsWith('/api/mention/resources?') && (init?.method || 'GET') === 'GET') {
      return Promise.resolve(Response.json({}))
    }
    return originalFetch(url, init)
  })
  pinia = createPinia()
  setActivePinia(pinia)
  const { useSessionRuntimeStore } = await server.ssrLoadModule('/src/modules/session/model/sessionRuntime.js')
  const runtime = useSessionRuntimeStore()
  t.mock.method(runtime, 'loadThread', async () => true)
  t.mock.method(runtime, 'refreshThreadHistory', readHistory)
  for (const [name, replacement] of Object.entries(options.runtime || {})) t.mock.method(runtime, name, replacement)
  const { threadApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
  const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
  t.mock.method(threadApi, 'getThreadAttachments', async () => ({ attachments: [] }))
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/index.js')
  useUserStore().userId = 'test-user'
  useUserStore().token = 'test-token'
  const { mcpApi } = await server.ssrLoadModule('/src/apis/mcp_api.js')
  t.mock.method(mcpApi, 'getMcpServers', async () => ({ success: true, data: [] }))
  t.mock.method(agentApi, 'getPublicThread', async (id) => ({ id, agent: { id: 'agent' }, yuxi: {} }))
  t.mock.method(agentApi, 'checkThreadWorkdir', async () => {})
  t.mock.method(threadApi, 'getThreads', async () => ({ data: [], has_more: false }))
  const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
  const agentStore = useAgentStore()
  agentStore.isInitialized = true
  const view = options.root ? 'SessionWorkspace' : 'SessionThreadView'
  const { default: component } = await server.ssrLoadModule(`/src/modules/session/ui/${view}.vue`)
  configureApiAuth({ getToken: () => 'test-token' })
  globalThis.document = { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible' }
  globalThis.window = { addEventListener() {}, removeEventListener() {}, innerWidth: 1200 }
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  app = renderer.createApp({ ...component, render: () => h('div') }, { isNewSession: true, active: true, ...(options.root ? { visible: true } : {}), ...options.props })
  app.provide(ssrContextKey, { modules: new Set() })
  app.use(pinia)
  app.use(options.router || createRouter({ history: createMemoryHistory(), routes: [] }))
  app.mount({})
  mounted = true
  const state = toRaw(app._instance.setupState)
  if (options.threadId) {
    if (options.root) state.mainThread.value = { threadId: options.threadId }
    else state.currentChatId.value = options.threadId
    runtime.getThreadState(options.threadId).detail = {
      id: options.threadId, agent: { id: 'agent' }, yuxi: {}
    }
  }
  if (options.send) t.mock.method(state.sessionInput, 'send', options.send)
  return { state, runtime, agentStore, agentApi, component, props: app._instance.props }
}
