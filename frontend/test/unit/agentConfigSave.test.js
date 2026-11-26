import { createMemoryHistory, createRouter, routerKey } from 'vue-router'
import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers'
import test from 'node:test'
import { createRenderer, ssrContextKey } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

test('共享智能体保存只提交修改字段，并使用后端合并结果更新基线', async () => {
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: () => null, setItem() {}, removeItem() {} }
  })
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
    const { agentApi } = await server.ssrLoadModule('/src/apis/index.js')
    const store = useAgentStore()
    const skills = Array.from({ length: 10 }, (_, i) => `skill-${i}`)
    const agent = {
      id: 123,
      agent_id: 'shared-agent',
      config_json: {
        context: { model: 'old-model', skills, preload_skills: null, mcps: null, knowledges: [] }
      },
      configurable_items: {
        skills: { type: 'list', kind: 'skills', options: skills.slice(0, 5) },
        preload_skills: {
          type: 'list',
          kind: 'skills',
          default: null,
          options: skills.slice(0, 5),
          x_oap_ui_config: { default: [] }
        }
      }
    }
    store.agentDetails[agent.agent_id] = agent
    await store.selectAgent(agent.agent_id)
    assert.deepEqual(store.agentConfig.preload_skills, [])
    assert.deepEqual(store.configurableItems.preload_skills.default, [])
    assert.equal(store.hasConfigChanges, false)
    store.updateAgentConfig({ model: 'new-model' })
    const requests = []
    agentApi.updateAgent = async (id, payload) => {
      requests.push({ id, payload: JSON.parse(JSON.stringify(payload)) })
      return {
        agent: {
          ...agent,
          config_json: {
            context: {
              ...agent.config_json.context,
              model: 'new-model',
              skills: [...skills, 'concurrent-skill']
            }
          }
        }
      }
    }

    await store.saveAgentConfig()

    assert.deepEqual(requests, [
      {
        id: 'shared-agent',
        payload: { config_json: { context: { model: 'new-model' } } }
      }
    ])
    assert.deepEqual(store.agentConfig.skills, [...skills, 'concurrent-skill'])
    assert.deepEqual(store.originalAgentConfig, store.agentConfig)
    assert.equal(store.hasConfigChanges, false)

    store.updateAgentConfig({ skills: [], knowledges: null })
    assert.deepEqual(store.changedAgentConfig, { skills: [], knowledges: null })
    agentApi.updateAgent = async () => {
      throw new Error('save rejected')
    }
    await assert.rejects(
      store.updateAgentProfile(agent.agent_id, {
        config_json: { context: store.changedAgentConfig }
      }),
      /save rejected/
    )
    assert.deepEqual(store.agentConfig.skills, [])
    assert.deepEqual(store.originalAgentConfig.skills, [...skills, 'concurrent-skill'])
    assert.equal(store.hasConfigChanges, true)
  } finally {
    await server.close()
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
  }
})

test('Agent 数字主键与路由身份不同，详情和创建更新删除始终按 agent_id 工作', async (t) => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem() {}, setItem() {} }
  })
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
    const { agentApi } = await server.ssrLoadModule('/src/apis/index.js')
    const agent = {
      id: 209,
      agent_id: 'preview',
      name: 'Preview',
      can_run: true,
      config_json: { context: { model: 'model-a', tools: ['calculator'] } },
      configurable_items: {
        model: { type: 'str' },
        tools: { type: 'list', options: ['calculator'] }
      }
    }
    const requested = []
    t.mock.method(agentApi, 'getAgentDetail', async (slug) => {
      requested.push(slug)
      return { agent }
    })
    const store = useAgentStore()
    await store.selectAgent('preview')
    assert.equal(store.selectedAgent.agent_id, 'preview')
    assert.equal(store.selectedAgent.id, 209)
    assert.deepEqual(Object.keys(store.agentDetails), ['preview'])
    assert.equal(store.agentConfig.model, 'model-a')
    assert.deepEqual(store.availableTools, ['calculator'])
    await store.fetchAgentDetail('preview')
    assert.deepEqual(requested, ['preview'], '同一 slug 的详情命中缓存')
    const created = { ...agent, id: 210, agent_id: 'created' }
    t.mock.method(agentApi, 'createAgent', async () => ({ agent: created }))
    await store.createAgent({ name: 'Created' })
    assert.equal(store.selectedAgentId, 'created')
    assert.equal(store.selectedAgent.id, 210)
    t.mock.method(agentApi, 'updateAgent', async (slug) => {
      assert.equal(slug, 'created')
      return { agent: { ...created, config_json: { context: { model: 'model-b' } } } }
    })
    await store.updateAgentProfile('created', {})
    assert.equal(store.agentConfig.model, 'model-b')
    assert.equal(store.agents.length, 1)
    t.mock.method(agentApi, 'deleteAgent', async (slug) => assert.equal(slug, 'created'))
    await store.deleteAgent('created')
    assert.deepEqual(store.agents, [])
    assert.equal(store.selectedAgentId, null)
    assert.equal(store.agentDetails.created, undefined)
  } finally {
    await server.close()
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous)
    else delete globalThis.localStorage
  }
})


test('会话导出按稳定 Agent 身份读取列表元数据', async (t) => {
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true, value: { getItem: () => null, setItem() {}, removeItem() {} }
  })
  const previousDocument = globalThis.document
  globalThis.document = { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible' }
  const pinia = createPinia()
  setActivePinia(pinia)
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  let app
  try {
    const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
    const store = useAgentStore(pinia)
    store.isInitialized = true
    store.agents = [{ id: 301, agent_id: 'export-agent', name: '导出智能体', description: '导出说明' }]
    const { threadApi } = await server.ssrLoadModule('/src/apis/index.js')
    t.mock.method(threadApi, 'getThreads', async () => ({ data: [], has_more: false }))
    const { useSessionRuntimeStore } = await server.ssrLoadModule('/src/modules/session/model/sessionRuntime.js')
    const runtime = useSessionRuntimeStore(pinia)
    const { default: component } = await server.ssrLoadModule('/src/modules/session/ui/SessionThreadView.vue')
    let state, exposed
    const renderer = createRenderer({
      createElement: () => ({ children: [] }), createText: () => ({}), createComment: () => ({}),
      insert: (child, parent) => parent.children.push(child), remove() {}, setText() {}, setElementText() {},
      parentNode: () => null, nextSibling: () => null, patchProp() {}
    })
    app = renderer.createApp({ setup() {
      state = component.setup({ agentId: 'export-agent', isNewSession: true, active: false, cooperationMembers: [] }, {
        expose(value) { exposed = value }, emit() {}
      })
      return () => null
    } })
    app.use(pinia)
    app.provide(routerKey, createRouter({ history: createMemoryHistory('/'), routes: [] }))
    app.provide(ssrContextKey, {})
    app.mount({ children: [] })
    await new Promise((resolve) => setImmediate(resolve))
    state.currentChatId.value = 'thread-export'
    runtime.getThreadState('thread-export').detail = { id: 'thread-export', agent: { id: 'export-agent' }, yuxi: { title: '导出会话' } }
    runtime.getThreadState('thread-export').optimisticInputs = { pending: [{ id: 'input', type: 'human', content: '正文', extra_metadata: {} }] }
    const payload = exposed.getExportPayload()
    assert.equal(payload.agentDescription, '导出说明')
    assert.equal(payload.agentName, '导出智能体')
    assert.equal(payload.chatTitle, '导出会话')
    assert.match(JSON.stringify(payload.messages), /正文/)
    assert.deepEqual(payload.messages, state.runGroups.value)
    assert.notEqual(payload.messages, state.runGroups.value)
  } finally {
    app?.unmount()
    if (previousDocument === undefined) delete globalThis.document
    else globalThis.document = previousDocument
    disposePinia(pinia)
    await server.close()
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
  }
})
