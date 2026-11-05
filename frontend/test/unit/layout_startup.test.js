import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createRenderer, h, ssrContextKey } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { setImmediate } from 'node:timers/promises'
import { createTestServer } from '../helpers/createTestServer.js'

/** 在公开请求边界控制能力发现，导航与知识库仍由真实布局装配。 */
async function mountLayout(t, discovery = async () => ({ capabilities: { features: { knowledge: true } } })) {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  let app
  t.after(async () => {
    app?.unmount()
    disposePinia(pinia)
    await server.close()
    delete globalThis.window
    delete globalThis.localStorage
  })
  const calls = [], requests = []
  t.mock.method(globalThis, 'fetch', async url => {
    requests.push(url)
    assert.equal(url, '/api/system/discovery')
    return Response.json(await discovery())
  })
  for (const [module, storeName, method, pending] of [
    ['settings/model/info', 'useInfoStore', 'loadInfoConfig', true],
    ['settings/model/config', 'useConfigStore', 'refreshConfig', false],
    ['session/model/chatThreads', 'useChatThreadsStore', 'loadThreads', false],
    ['projects/model/projects', 'useProjectsStore', 'loadProjects', false]
  ]) {
    const exports = await server.ssrLoadModule(`/src/modules/${module}.js`)
    const store = exports[storeName]()
    t.mock.method(store, method, () => {
      calls.push(method)
      return pending ? new Promise(() => {}) : Promise.resolve()
    })
  }
  const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
  useAgentStore().isInitialized = true
  const { default: Layout } = await server.ssrLoadModule('/src/app/layouts/AppLayout.vue')
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
  app = renderer.createApp({ ...Layout, render: () => h('div') })
  app.provide(ssrContextKey, { modules: new Set() })
  app.use(pinia)
  app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
  const { useKnowledgeBaseStore } = await server.ssrLoadModule('/src/modules/knowledge/model/knowledgeBase.js')
  const knowledgeBase = app.runWithContext(() => useKnowledgeBaseStore())
  t.mock.method(knowledgeBase, 'loadKnowledgeBases', () => {
    calls.push('loadKnowledgeBases')
    return new Promise(() => {})
  })
  const interval = t.mock.method(globalThis, 'setInterval', () => 123)
  const clear = t.mock.method(globalThis, 'clearInterval', () => {})
  app.mount({})
  await Promise.resolve()
  await setImmediate()
  return { calls, requests, interval, clear, unmount() { app.unmount(); app = null } }
}

test('布局导航不等待品牌或知识库，卸载后清理状态同步计时器', async (t) => {
  const h = await mountLayout(t)
  for (const method of ['loadInfoConfig', 'loadKnowledgeBases', 'loadThreads', 'loadProjects', 'refreshConfig']) {
    assert.ok(h.calls.includes(method), method)
  }
  assert.equal(h.calls.filter(method => method === 'loadKnowledgeBases').length, 1, '能力开启时读取一次布局管理列表')
  assert.deepEqual(h.requests, ['/api/system/discovery'])
  assert.equal(h.interval.mock.callCount(), 1)
  h.unmount()
  assert.equal(h.clear.mock.calls[0].arguments[0], 123)
})

test('知识库关闭或能力未明确开启时跳过布局管理列表，普通导航继续加载', async (t) => {
  for (const features of [{ knowledge: false }, {}]) {
    await t.test(JSON.stringify(features), async t => {
      const h = await mountLayout(t, async () => ({ capabilities: { features } }))
      assert.equal(h.calls.includes('loadKnowledgeBases'), false)
      assert.ok(h.calls.includes('loadThreads'))
      assert.ok(h.calls.includes('loadProjects'))
    })
  }
})

test('能力发现失败时跳过布局知识库管理列表，导航和配置仍完成加载', async (t) => {
  const h = await mountLayout(t, async () => { throw new Error('discovery unavailable') })
  assert.equal(h.calls.includes('loadKnowledgeBases'), false)
  assert.ok(h.calls.includes('loadThreads'))
  assert.ok(h.calls.includes('loadProjects'))
  assert.ok(h.calls.includes('refreshConfig'))
})

test('能力发现仍在等待时，导航不被阻塞且尚不读取布局管理列表', async (t) => {
  const h = await mountLayout(t, () => new Promise(() => {}))
  assert.equal(h.calls.includes('loadKnowledgeBases'), false)
  assert.ok(h.calls.includes('loadThreads'))
  assert.ok(h.calls.includes('loadProjects'))
})
