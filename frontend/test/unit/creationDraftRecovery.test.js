import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, h, nextTick, ssrContextKey, toRaw } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createTestServer } from '../helpers/createTestServer.js'

/** 装配真实创建组件，只替换渲染宿主与外部 API。 */
async function mountCreation(t, path, props = {}) {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const server = await createTestServer({ server: { middlewareMode: true }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'creation-feedback-boundary', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue') return '\0creation-feedback' },
    load(id) {
      if (id === '\0creation-feedback') return 'export const confirmations = []; export const Modal = { confirm: (options) => confirmations.push(options) }; export const message = { warning() {}, error() {}, success() {}, info() {} }'
      if (id.endsWith('/shared/model/theme.js')) return 'export const useThemeStore = () => ({ isDark: false })'
    }
  }] })
  const pinia = createPinia()
  setActivePinia(pinia)
  const { typeApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  t.mock.method(typeApi, 'getChunkPresets', async () => ({ chunk_presets: [] }))
  const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
  const agentStore = useAgentStore()
  agentStore.selectedAgentId = 'fixture-agent'
  agentStore.agents = [{ agent_id: 'fixture-agent', can_run: true }]
  t.mock.method(agentStore, 'selectAgent', async () => {})
  const component = (await server.ssrLoadModule(path)).default
  const { confirmations } = await server.ssrLoadModule('\0creation-feedback')
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  const app = renderer.createApp({ ...component, render: () => h('div') }, props)
  app.provide(ssrContextKey, { modules: new Set() })
  app.use(pinia)
  app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
  app.mount({})
  t.after(async () => { app.unmount(); disposePinia(pinia); await server.close(); delete globalThis.localStorage })
  return { state: toRaw(app._instance.setupState), props: app._instance.props, confirmations }
}

test('新建智能体的未修改表单直接关闭，草稿取消放弃保持输入，确认才关闭', async (t) => {
  const { state: s, confirmations } = await mountCreation(t, '/src/modules/agents/ui/AgentEditModal.vue')
  s.openCreate()
  await s.closeAgentModal()
  assert.equal(s.showAgentModal.value, false)
  assert.equal(confirmations.length, 0)
  for (const mutate of [
    () => { s.agentForm.name = '审查测试草稿' },
    () => { s.createContext.value = { temperature: 0.2 } },
    () => { s.createSkillFile.value = { name: 'draft.zip', size: 10 } }
  ]) {
    s.openCreate()
    mutate()
    const before = s.snapshotCreateDraft()
    let closing = s.closeAgentModal()
    assert.equal(s.showAgentModal.value, true)
    assert.equal(confirmations.length, 1)
    confirmations.shift().onCancel()
    await closing
    assert.equal(s.snapshotCreateDraft(), before)
    assert.equal(s.showAgentModal.value, true)
    closing = s.closeAgentModal()
    confirmations.shift().onOk()
    await closing
    assert.equal(s.showAgentModal.value, false)
  }
  s.openCreate()
  s.agentForm.name = 'saving'
  s.saving.value = true
  await s.closeAgentModal()
  assert.equal(s.showAgentModal.value, true)
  assert.equal(confirmations.length, 0, '保存中不能进入放弃流程')
})

test('知识库草稿跨步骤保留，取消确认不关闭，空表单无需确认', async (t) => {
  const emitted = []
  const { state: s, props, confirmations } = await mountCreation(t, '/src/modules/knowledge/ui/KnowledgeBaseCreateFlowModal.vue', {
    open: false, supportedKbTypes: { milvus: { requires_embedding_model: false } },
    'onUpdate:open': (open) => emitted.push(open)
  })
  props.open = true
  await nextTick()
  await s.handleCancel()
  assert.deepEqual(emitted, [false])
  assert.equal(confirmations.length, 0)
  emitted.length = 0
  s.form.name = '知识库测试草稿'
  s.goNext()
  assert.equal(s.currentStep.value, 1)
  let closing = s.handleCancel()
  assert.deepEqual(emitted, [])
  confirmations.shift().onCancel()
  await closing
  assert.equal(s.form.name, '知识库测试草稿')
  assert.equal(s.currentStep.value, 1)
  assert.deepEqual(emitted, [])
  closing = s.handleCancel()
  confirmations.shift().onOk()
  await closing
  assert.deepEqual(emitted, [false])
})
