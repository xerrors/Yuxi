import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('共享智能体保存只提交修改字段，并使用后端合并结果更新基线', async () => {
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: () => null, setItem() {}, removeItem() {} }
  })
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    const { agentApi } = await server.ssrLoadModule('/src/apis/index.js')
    const store = useAgentStore()
    const skills = Array.from({ length: 10 }, (_, i) => `skill-${i}`)
    const agent = {
      id: 'shared-agent',
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
    store.agentDetails[agent.id] = agent
    await store.selectAgent(agent.id)
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
      store.updateAgentProfile(agent.id, {
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
