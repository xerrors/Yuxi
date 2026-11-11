import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { createRenderer, h, ssrContextKey, toRaw } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createTestServer } from './createTestServer.js'

/** 装配会话页面，保留真实响应式状态并控制历史读取边界。 */
export async function mountSessionWorkspace(t, fetchThreadMessages = async () => {}, options = {}) {
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
        if (importer?.includes('/session/ui/SessionWorkspace.vue') && id.endsWith('/AgentInputArea.vue')) return '\0session-workspace-input'
        if (importer?.includes('/session/ui/SessionWorkspace.vue') && id.endsWith('.vue')) return '\0session-workspace-child'
      },
      load(id) {
        if (id === '\0session-workspace-input') return "import { h } from 'vue'; export default { setup(_, { attrs }) { return () => h('input', { ...attrs, 'data-session-input': '' }) } }"
        if (id === '\0session-workspace-child') return 'export default { render: () => null }'
      }
    }] : []
  })
  pinia = createPinia()
  setActivePinia(pinia)
  const { useSessionRuntimeStore } = await server.ssrLoadModule('/src/modules/session/model/sessionRuntime.js')
  const runtime = useSessionRuntimeStore()
  t.mock.method(runtime, 'fetchThreadMessages', fetchThreadMessages)
  for (const [name, replacement] of Object.entries(options.runtime || {})) t.mock.method(runtime, name, replacement)
  const { threadApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
  const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
  const { mcpApi } = await server.ssrLoadModule('/src/apis/mcp_api.js')
  t.mock.method(mcpApi, 'getMcpServers', async () => ({ success: true, data: [] }))
  t.mock.method(agentApi, 'getPublicThread', async (id) => ({ id, agent: { id: 'agent' }, yuxi: {} }))
  t.mock.method(threadApi, 'getThreads', async () => ({ data: [], has_more: false }))
  const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
  const agentStore = useAgentStore()
  agentStore.isInitialized = true
  const { default: component } = await server.ssrLoadModule('/src/modules/session/ui/SessionWorkspace.vue')
  globalThis.document = { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible' }
  globalThis.window = { addEventListener() {}, removeEventListener() {}, innerWidth: 1200 }
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  app = renderer.createApp({ ...component, render: () => h('div') }, { isNewSession: true, visible: true, ...options.props })
  app.provide(ssrContextKey, { modules: new Set() })
  app.use(pinia)
  app.use(options.router || createRouter({ history: createMemoryHistory(), routes: [] }))
  app.mount({})
  mounted = true
  const state = toRaw(app._instance.setupState)
  if (options.threadId) state.currentThreadId.value = options.threadId
  if (options.send) t.mock.method(state.sessionInput, 'send', options.send)
  return { state, runtime, agentStore, agentApi, component, props: app._instance.props }
}
