import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { createRenderer, h, ssrContextKey, toRaw } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createTestServer } from './createTestServer.js'

/** 装配真实阅读组件，保留 runtime 和视口生命周期。 */
export async function mountThreadTimeline(t, loadOlderHistory = async () => {}, options = {}) {
  const globals = Object.fromEntries(
    ['localStorage', 'document', 'window'].map((name) => [
      name,
      Object.getOwnPropertyDescriptor(globalThis, name)
    ])
  )
  const previousPinia = getActivePinia()
  let server,
    pinia,
    app,
    mounted = false
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
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  pinia = createPinia()
  setActivePinia(pinia)
  const { useSessionRuntimeStore } = await server.ssrLoadModule(
    '/src/modules/session/model/sessionRuntime.js'
  )
  const runtime = useSessionRuntimeStore()
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/index.js')
  useUserStore().token = 'test-reader'
  t.mock.method(runtime, 'loadThread', async () => false)
  t.mock.method(runtime, 'loadOlderHistory', loadOlderHistory)
  for (const [name, replacement] of Object.entries(options.runtime || {}))
    t.mock.method(runtime, name, replacement)
  const { default: component } = await server.ssrLoadModule(
    '/src/modules/session/ui/ThreadTimeline.vue'
  )
  globalThis.document = {
    addEventListener() {},
    removeEventListener() {},
    visibilityState: 'visible'
  }
  globalThis.window = { addEventListener() {}, removeEventListener() {}, innerWidth: 1200 }
  const renderer = createRenderer({
    createElement: () => ({}),
    createText: () => ({}),
    createComment: () => ({}),
    insert() {},
    remove() {},
    setText() {},
    setElementText() {},
    patchProp() {},
    parentNode: () => null,
    nextSibling: () => null
  })
  app = renderer.createApp(
    { ...component, render: () => h('div') },
    { threadId: 'thread', ...options.props }
  )
  app.provide(ssrContextKey, { modules: new Set() })
  app.use(pinia)
  app.use(options.router || createRouter({ history: createMemoryHistory(), routes: [] }))
  app.mount({})
  mounted = true
  const state = toRaw(app._instance.setupState)
  return { state, runtime, component, props: app._instance.props }
}
