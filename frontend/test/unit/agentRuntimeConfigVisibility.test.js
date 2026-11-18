import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createSSRApp, h, reactive, ref } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

let server, component
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  ;({ default: component } = await server.ssrLoadModule('/src/modules/agents/ui/AgentRuntimeConfigForm.vue'))
})
after(async () => { await server?.close() })

/** 使用真实组件 setup 验证筛选结果，保留响应式资源与配置。 */
async function createForm(selection) {
  const item = { name: '知识库', kind: 'knowledges', type: 'list', supports_all: true, options: [] }
  const state = {
    selectedAgent: ref({ can_manage: true }),
    selectedAgentId: ref('test-agent'),
    agentConfig: ref({ knowledges: selection }),
    configurableItems: ref({ knowledges: item })
  }
  const props = reactive({ modelValue: state.agentConfig.value, configurableItems: state.configurableItems.value,
    segment: 'tools', showSegmented: false, readonly: false, creatableResourceKinds: [] })
  let form
  await renderToString(createSSRApp({ setup() {
    form = component.setup(props, { expose() {}, emit(name, value) {
      if (name === 'update:modelValue') { props.modelValue = value; state.agentConfig.value = value }
    } })
    return () => h('div')
  } }))
  return { state, form, props }
}

for (const selection of ['all', []]) {
  test(`空资源分组隐藏且保留配置 ${JSON.stringify(selection)}，候选项新增后显示`, async () => {
    const { state, form } = await createForm(selection)
    assert.deepEqual(form.filteredConfigurableItems.value, {})
    assert.equal(form.isCurrentSegmentEmpty.value, true)
    assert.deepEqual(state.agentConfig.value.knowledges, selection)
    state.configurableItems.value.knowledges.options.push({ value: 'kb-a', label: '知识库 A' })
    assert.ok(form.filteredConfigurableItems.value.knowledges)
    assert.equal(form.isCurrentSegmentEmpty.value, false)
    assert.deepEqual(state.agentConfig.value.knowledges, selection)
  })
}

test('没有候选项时仍保留历史固定选择的提示与清空入口', async () => {
  const { state, form } = await createForm(['hidden-kb'])
  assert.ok(form.filteredConfigurableItems.value.knowledges)
  assert.deepEqual(state.agentConfig.value.knowledges, ['hidden-kb'])
})


test('Context 默认值仅展示，字段修改只提交用户覆盖；只读不改变草稿', async () => {
  const { state, form, props } = await createForm(undefined)
  props.modelValue = {}
  props.configurableItems = { mcps: { type: 'list', kind: 'mcps', default: 'all', options: [] },
    system_prompt: { type: 'str', kind: 'prompt', default: '角色' }, flag: { type: 'boolean', default: true } }
  assert.equal(form.agentConfig.value.flag, true)
  assert.equal(form.agentConfig.value.mcps, 'all')
  assert.deepEqual(props.modelValue, {})
  form.updateConfigValue('system_prompt', '新角色')
  assert.deepEqual(state.agentConfig.value, { system_prompt: '新角色' })
  props.readonly = true
  form.updateConfigValue('system_prompt', '不允许覆盖')
  assert.deepEqual(state.agentConfig.value, { system_prompt: '新角色' })
})

test('管理员没有 MCP 候选时仍能看到创建入口对应的资源分组', async () => {
  const { form, props } = await createForm([])
  props.configurableItems = { mcps: { type: 'list', kind: 'mcps', default: 'all', options: [] } }
  assert.deepEqual(form.filteredConfigurableItems.value, {})
  props.creatableResourceKinds = ['mcps']
  assert.ok(form.filteredConfigurableItems.value.mcps)
})

test('插件在工具分组独立选择，知识库警告取决于插件而非同名 Skill', async () => {
  const { form, props } = await createForm(['kb-a'])
  props.configurableItems = {
    plugins: { name: '插件', type: 'list', kind: 'plugins', default: [], supports_all: false,
      options: [{ key: 'knowledge-base', name: '知识库' }] },
    knowledges: { type: 'list', kind: 'knowledges', default: 'all', options: [] }
  }
  props.modelValue = { knowledges: ['kb-a'], skills: ['knowledge-base'] }
  assert.ok(form.filteredConfigurableItems.value.plugins)
  assert.equal(form.shouldShowKnowledgePluginWarning('knowledges'), true)
  form.updateConfigValue('plugins', ['knowledge-base'])
  assert.deepEqual(props.modelValue.plugins, ['knowledge-base'])
  assert.equal(form.shouldShowKnowledgePluginWarning('knowledges'), false)
  form.updateConfigValue('plugins', [])
  assert.equal(form.shouldShowKnowledgePluginWarning('knowledges'), true)
})

test('直接选择知识库 MCP 时无需知识库插件，其他 MCP 不会消除警告', async () => {
  const { form, props } = await createForm(['kb-a'])
  props.modelValue = { knowledges: ['kb-a'], plugins: [], mcps: ['knowledge'] }
  assert.equal(form.shouldShowKnowledgePluginWarning('knowledges'), false)
  props.modelValue = { knowledges: ['kb-a'], plugins: [], mcps: 'all' }
  assert.equal(form.shouldShowKnowledgePluginWarning('knowledges'), false)
  props.modelValue = { knowledges: ['kb-a'], plugins: [], mcps: ['other'] }
  assert.equal(form.shouldShowKnowledgePluginWarning('knowledges'), true)
  props.modelValue = { knowledges: ['kb-a'], plugins: [], mcps: [] }
  assert.equal(form.shouldShowKnowledgePluginWarning('knowledges'), true)
})
