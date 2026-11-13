import assert from 'node:assert/strict'
import test, { afterEach } from 'node:test'
import { createRenderer, nextTick, ref, ssrContextKey } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { matchedRouteKey, routerKey } from 'vue-router'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

const cleanups = []
afterEach(async () => { while (cleanups.length) await cleanups.pop()() })
const renderer = createRenderer({
  createElement: () => ({ children: [] }), createText: () => ({}), createComment: () => ({}),
  insert: (child, parent) => parent.children.push(child), remove() {}, setText() {}, setElementText() {},
  parentNode: () => null, nextSibling: () => null, patchProp() {}
})
let currentServer

/** 在真实组件和 Store 中隔离接口及路由边界。 */
async function setupForm(detail, isAdmin = true) {
  const writes = [], errors = [], navigations = []
  const previousStorage = globalThis.localStorage
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const pinia = createPinia()
  setActivePinia(pinia)
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  currentServer = server
  const { agentApi, skillApi } = await server.ssrLoadModule('/src/apis/index.js')
  const original = { getAgentDetail: agentApi.getAgentDetail, getAgentBackendDetail: agentApi.getAgentBackendDetail,
    createAgent: agentApi.createAgent, updateAgent: agentApi.updateAgent,
    uploadAgentBoundSkill: agentApi.uploadAgentBoundSkill, getAgentBoundSkill: agentApi.getAgentBoundSkill }
  agentApi.getAgentDetail = async () => detail
  agentApi.getAgentBackendDetail = async () => ({ configurable_items: {
    mcps: { name: 'MCP', kind: 'mcps', type: 'list', supports_all: true, default: 'all', options: [{ key: 'ready', name: '可用' }] },
    skills: { name: 'Skill', kind: 'skills', type: 'list', supports_all: true, default: 'all', options: [] },
    system_prompt: { kind: 'prompt', type: 'str', default: '后端角色' }
  } })
  agentApi.updateAgent = async (slug, payload) => {
    writes.push({ slug, payload: JSON.parse(JSON.stringify(payload)) })
    return { agent: { ...detail, ...payload, agent_id: slug } }
  }
  agentApi.createAgent = async (payload, file) => {
    writes.push({ payload: JSON.parse(JSON.stringify(payload)), file })
    return { agent: { ...payload, agent_id: payload.slug, can_run: true } }
  }
  const oldPrepare = skillApi.prepareSkillUpload
  agentApi.getAgentBoundSkill = async () => ({ skill: null })
  skillApi.prepareSkillUpload = async () => ({ data: { draft_id: 'draft', items: [{ slug: 'shared-guide' }] } })
  const { useAgentStore } = await server.ssrLoadModule('/src/modules/agents/model/agent.js')
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const store = useAgentStore(pinia)
  const user = useUserStore(pinia)
  user.userRole = isAdmin ? 'superadmin' : 'user'
  user.uid = 'admin'
  user.departmentId = 1
  const oldMessage = { error: message.error, warning: message.warning, success: message.success, info: message.info }
  Object.assign(message, { error: (value) => errors.push(value), warning: (value) => errors.push(value), success() {}, info: (value) => errors.push(value) })
  const record = { leaveGuards: new Set() }
  const router = { push: async (route) => { navigations.push(route) } }
  const { default: component } = await server.ssrLoadModule('/src/modules/agents/ui/AgentEditModal.vue')
  let form
  const app = renderer.createApp({ setup() {
    form = component.setup({ backendOptions: [] }, { expose() {}, emit() {} })
    return () => null
  } })
  app.use(pinia)
  app.provide(ssrContextKey, {})
  app.provide(routerKey, router)
  app.provide(matchedRouteKey, ref(record))
  app.mount({ children: [] })
  cleanups.push(async () => {
    app.unmount()
    Object.assign(agentApi, original)
    skillApi.prepareSkillUpload = oldPrepare
    Object.assign(message, oldMessage)
    disposePinia(pinia)
    await server.close()
    if (previousStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = previousStorage
  })
  return { form, store, writes, errors, navigations, routeGuards: [...record.leaveGuards], deps: { agentApi, skillApi } }
}

async function setupBoundPanel(form) {
  const { default: component } = await currentServer.ssrLoadModule('/src/modules/agents/ui/AgentBoundSkillPanel.vue')
  let panel
  const app = renderer.createApp({ setup() {
    panel = component.setup({ agentSlug: 'bound-parent' }, {
      expose() {}, emit: (event, value) => { if (event === 'busy') form.boundSkillSaving.value = value }
    })
    return () => null
  } })
  app.provide(ssrContextKey, {})
  app.mount({ children: [] })
  cleanups.push(() => app.unmount())
  return panel
}

test('管理员打开坏配置不写入，明确保存以 slug 修复共享范围', async () => {
  const detail = {
    id: 90, agent_id: 'fixture-agent', slug: 'fixture-agent', name: '权限修复',
    backend_id: 'ChatbotAgent', can_manage: true, can_share: true, visibility: 'shared', share_config_invalid: true,
    share_config: { version: 2, read_scope: { access_level: 'department', department_ids: { 1: true } } }
  }
  const { form, store, writes, errors } = await setupForm(detail)
  await form.openEdit({ agent_id: 'fixture-agent' })
  assert.equal(form.showAgentModal.value, true)
  assert.equal(form.shareConfigNeedsRepair.value, true)
  assert.deepEqual(writes, [])
  assert.equal(store.selectedAgentId, 'fixture-agent')
  assert.deepEqual(form.agentShareConfig.value, { version: 2, read_scope: null, manage_scope: null })
  await form.saveAgent()
  assert.deepEqual(errors, [])
  assert.equal(writes.length, 1)
  assert.equal(writes[0].slug, 'fixture-agent')
  assert.deepEqual(writes[0].payload.share_config, { version: 2, read_scope: null, manage_scope: null })
  assert.equal(form.showAgentModal.value, false)
  assert.equal(form.shareConfigNeedsRepair.value, false)
})

test('普通用户的私有智能体不提交共享配置，也不提供共享入口', async () => {
  const { form } = await setupForm({
    id: 91, agent_id: 'private-admin', name: '私有', backend_id: 'ChatbotAgent',
    visibility: 'private', can_manage: true, can_share: false,
    share_config: { version: 2, read_scope: null, manage_scope: null }
  }, false)
  await form.openEdit({ agent_id: 'private-admin' })
  assert.equal(form.hasAnyUnsavedChanges.value, false)
  assert.equal(Boolean(form.canEditAgentShareConfig.value), false)
  assert.equal('share_config' in form.buildAgentPayload(), false)
  assert.equal('publishing' in form, false)
})

test('管理员创建 Agent 默认个人使用，切换团队共享设置权限范围', async () => {
  const { form } = await setupForm({})
  form.openCreate()
  form.agentForm.name = '资料助手'
  assert.equal(Boolean(form.canEditAgentShareConfig.value), true)
  assert.equal(form.buildAgentPayload().visibility, 'private')
  assert.equal('share_config' in form.buildAgentPayload(), false)
  form.agentForm.visibility = 'shared'
  assert.deepEqual(form.buildAgentPayload().share_config.read_scope, { access_level: 'global', department_ids: [], user_uids: [] })
  assert.equal(form.buildAgentPayload().visibility, 'shared')
  assert.equal(form.buildAgentPayload().share_config.read_scope.access_level, 'global')
  await form.toggleCreateAdvanced()
  await form.toggleCreateAdvanced()
  assert.equal(form.isAgentShared.value, true)
  form.agentForm.visibility = 'private'
  assert.equal('share_config' in form.buildAgentPayload(), false)
  form.agentForm.visibility = 'shared'
  assert.equal(form.buildAgentPayload().share_config.read_scope.access_level, 'global')
  form.openCreate()
  assert.equal(form.buildAgentPayload().visibility, 'private')
  assert.equal('share_config' in form.buildAgentPayload(), false)
})

test('管理员将已有私有 Agent 共享，脏状态与保存都使用原定义', async () => {
  const { form, writes } = await setupForm({
    id: 92, agent_id: 'owned-private', name: '私有助手', backend_id: 'ChatbotAgent',
    visibility: 'private', can_manage: true, can_share: true,
    share_config: { version: 2, read_scope: null, manage_scope: null }
  })
  await form.openEdit('owned-private')
  assert.equal(form.agentShareConfig.value.read_scope.access_level, 'user')
  assert.equal(form.hasProfileChanges.value, false)
  assert.equal('visibility' in form.buildAgentPayload(), false)
  assert.equal('share_config' in form.buildAgentPayload(), false)
  form.agentForm.visibility = 'shared'
  assert.equal(form.hasProfileChanges.value, true)
  assert.deepEqual(form.buildAgentPayload().share_config.read_scope.user_uids, ['admin'])
  form.agentForm.visibility = 'private'
  assert.equal(form.hasProfileChanges.value, false)
  form.agentForm.visibility = 'shared'
  await form.saveAgent()
  assert.equal(writes[0].slug, 'owned-private')
  assert.equal(writes[0].payload.visibility, 'shared')
  assert.equal(writes[0].payload.share_config.version, 2)
})

test('已有共享定义只编辑共享范围，普通用户创建始终不发送授权', async () => {
  const { form } = await setupForm({
    agent_id: 'shared', name: '共享助手', backend_id: 'ChatbotAgent',
    visibility: 'shared', can_manage: true, can_share: true,
    share_config: { version: 2, read_scope: { access_level: 'global' }, manage_scope: null }
  })
  await form.openEdit('shared')
  assert.equal(form.canToggleAgentSharing.value, false)
  assert.equal('visibility' in form.buildAgentPayload(), false)
  const { form: privateForm } = await setupForm({}, false)
  privateForm.openCreate()
  assert.equal(Boolean(privateForm.canEditAgentShareConfig.value), false)
  assert.equal(privateForm.buildAgentPayload().visibility, 'private')
  assert.equal('share_config' in privateForm.buildAgentPayload(), false)
})

test('编辑专属 Skill 保留未保存 Agent 配置，忙碌时不跳转，保存后才离开', async () => {
  const { form, store, navigations, errors } = await setupForm({
    id: 237, agent_id: 'guide-agent', name: '资料整理助手', backend_id: 'ChatbotAgent',
    visibility: 'private', can_manage: true, can_share: false, can_run: true
  })
  await form.openEdit({ agent_id: 'guide-agent' })
  const route = { name: 'ExtensionSkillDetail', params: { slug: 'self-guide' } }
  form.agentForm.name = '未保存名称'
  await form.navigateToSkill(route)
  assert.equal(form.showAgentModal.value, true)
  assert.equal(form.agentForm.name, '未保存名称')
  assert.deepEqual(navigations, [])
  assert.match(errors.pop(), /请先保存智能体配置/)
  form.agentForm.name = '资料整理助手'
  store.updateAgentConfig({ system_prompt: '未保存' })
  await form.navigateToSkill(route)
  assert.deepEqual(navigations, [])
  store.resetAgentConfig()
  form.saving.value = true
  await form.navigateToSkill(route)
  assert.deepEqual(navigations, [])
  form.saving.value = false
  form.agentIconUploading.value = true
  await form.navigateToSkill(route)
  assert.deepEqual(navigations, [])
  form.agentIconUploading.value = false
  await form.navigateToSkill(route)
  assert.deepEqual(navigations, [route])
  assert.equal(form.showAgentModal.value, false)
})

test('创建提交完整 Context 与专属 ZIP，422 保留草稿而不发送资源定义', async () => {
  const { form, writes, deps } = await setupForm({})
  form.openCreate()
  assert.equal(form.createAdvancedOpen.value, false)
  await form.toggleCreateAdvanced()
  assert.equal(form.createConfigItems.value.mcps.options[0].key, 'ready')
  assert.deepEqual(form.createContext.value, {}, '展示默认配置不制造持久覆盖')
  const file = { name: 'guide.zip', size: 100 }
  form.beforeCreateSkillUpload(file)
  form.runtimeConfig.value = { system_prompt: '指定角色', model: 'provider:model', mcps: ['ready'], skills: [] }
  const context = { ...form.runtimeConfig.value }
  const create = deps.agentApi.createAgent
  deps.agentApi.createAgent = async () => { throw Object.assign(new Error('invalid'), { status: 422 }) }
  await form.saveAgent()
  assert.equal(form.showAgentModal.value, true)
  assert.equal(form.createSkillFile.value.name, file.name)
  assert.deepEqual(form.createContext.value, context)
  assert.equal(form.createError.value, 'invalid')
  deps.agentApi.createAgent = create
  await form.saveAgent()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].file.name, 'guide.zip')
  assert.deepEqual(writes[0].payload.config_json.context, context)
  assert.equal('mcp_servers' in writes[0].payload, false)
})

test('等待时不重复创建，未知结果禁止在同一草稿直接重试，明确校验失败可修正', async () => {
  const { form, writes, deps } = await setupForm({ agent_id: 'existing', name: 'Existing', can_manage: true, can_run: true })
  form.openCreate()
  let rejectRequest
  deps.agentApi.createAgent = () => {
    writes.push('sent')
    return new Promise((_, reject) => { rejectRequest = reject })
  }
  const request = form.saveAgent()
  await form.saveAgent()
  assert.equal(writes.length, 1)
  rejectRequest(Object.assign(new Error('invalid'), { status: 422 }))
  await request
  assert.equal(form.createOutcomeUnknown.value, false)
  assert.equal(form.showAgentModal.value, true)
  deps.agentApi.createAgent = async () => {
    writes.push('sent')
    throw new TypeError('connection lost')
  }
  await form.saveAgent()
  assert.equal(form.createOutcomeUnknown.value, true)
  const advancedBefore = form.createAdvancedOpen.value
  await form.toggleCreateAdvanced()
  assert.equal(form.createAdvancedOpen.value, advancedBefore)
  form.openMcpCreate()
  await form.beforeSharedSkillUpload({ name: 'shared.zip', size: 100 })
  assert.equal(form.mcpCreateOpen.value, false)
  assert.equal(form.sharedSkillOpen.value, false)
  await form.saveAgent()
  assert.equal(writes.length, 2)
  await form.closeAgentModal()
  await form.openEdit({ agent_id: 'existing' })
  form.agentForm.name = 'Updated existing'
  await form.saveAgent()
  assert.equal(writes[2].slug, 'existing')
  assert.equal(writes[2].payload.name, 'Updated existing')
})

test('基本信息与高级配置共用一份草稿，等待时不能切换，返回后可直接创建', async () => {
  const { form, writes } = await setupForm({})
  form.openCreate()
  form.agentForm.name = '资料助手'
  form.beforeCreateSkillUpload({ name: 'guide.zip', size: 100 })
  await form.toggleCreateAdvanced()
  form.agentForm.slug = 'research-assistant'
  form.runtimeConfig.value = { system_prompt: '整理资料', mcps: [] }
  form.saving.value = true
  await form.toggleCreateAdvanced()
  assert.equal(form.createAdvancedOpen.value, true)
  form.saving.value = false
  await form.toggleCreateAdvanced()
  assert.equal(form.createAdvancedOpen.value, false)
  assert.equal(form.agentForm.name, '资料助手')
  assert.equal(form.createSkillFile.value.name, 'guide.zip')
  assert.deepEqual(form.createContext.value, { system_prompt: '整理资料', mcps: [] })
  await form.saveAgent()
  assert.equal(writes[0].file.name, 'guide.zip')
  assert.equal(writes[0].payload.slug, 'research-assistant')
  assert.equal(writes[0].payload.config_json.context.system_prompt, '整理资料')
})

for (const invalid of ['name', 'share']) {
  test(`高级区域的${invalid}校验返回基本区域并聚焦`, async () => {
    const { form, writes } = await setupForm({})
    form.openCreate()
    form.agentForm.name = invalid === 'name' ? ' ' : '资料助手'
    if (invalid === 'share') form.agentForm.visibility = 'shared'
    await form.toggleCreateAdvanced()
    let focused = false
    const target = { focus() { focused = true } }
    if (invalid === 'name') form.agentNameInputRef.value = target
    else {
      form.shareConfigHeadingRef.value = target
      form.agentShareConfigFormRef.value = { validate: () => ({ valid: false, message: '请选择共享范围' }) }
    }
    form.runtimeConfig.value = { system_prompt: '保留草稿' }
    await form.saveAgent()
    assert.equal(form.createAdvancedOpen.value, false)
    assert.equal(focused, true)
    assert.equal(form.createError.value, invalid === 'name' ? '请填写智能体名称' : '请选择共享范围')
    assert.deepEqual(form.createContext.value, { system_prompt: '保留草稿' })
    assert.deepEqual(writes, [])
  })
}

test('创建 ZIP 后缀和预算在上传前拒绝，普通用户不提交导入配置', async () => {
  const { form, errors } = await setupForm({}, false)
  form.openCreate()
  form.beforeCreateSkillUpload({ name: 'bad.md', size: 1 })
  form.beforeCreateSkillUpload({ name: 'big.zip', size: 10 * 1024 * 1024 + 1 })
  assert.equal(form.createSkillFile.value, null)
  assert.equal(errors.length, 2)
  assert.equal('mcp_servers' in form.buildAgentPayload(), false)
})

for (const outcome of ['pending', 'unknown', 'invalid']) {
  const description = { pending: '等待中的', unknown: '结果不明的', invalid: '校验失败后的' }[outcome]
  test(`旧编辑请求不能覆盖${description}创建草稿`, async () => {
    const detail = { agent_id: 'existing', name: 'Existing', can_manage: true, can_run: true }
    const { form, deps } = await setupForm(detail)
    const detailReads = []
    deps.agentApi.getAgentDetail = () => new Promise((resolve) => detailReads.push(resolve))
    const opens = [form.openEdit('existing')]
    form.openCreate()
    form.agentForm.name = 'Creation draft'
    form.agentForm.slug = 'creation-draft'
    form.beforeCreateSkillUpload({ name: 'draft.zip', size: 100 })
    let rejectCreation
    deps.agentApi.createAgent = () => new Promise((_, reject) => { rejectCreation = reject })
    const request = form.saveAgent()
    try {
      form.openCreate()
      opens.push(form.openEdit('another'))
      assert.equal(form.agentForm.name, 'Creation draft')
      assert.equal(detailReads.length, 1)
      if (outcome !== 'pending') {
        rejectCreation(outcome === 'unknown'
          ? new TypeError('connection lost')
          : Object.assign(new Error('invalid'), { status: 422 }))
        await request
      }
      if (outcome === 'unknown') {
        form.openCreate()
        opens.push(form.openEdit('another'))
        assert.equal(detailReads.length, 1)
      }
      detailReads[0](detail)
      await opens[0]
      assert.equal(form.editingAgentId.value, null)
      assert.equal(form.agentForm.name, 'Creation draft')
      assert.equal(form.createSkillFile.value.name, 'draft.zip')
      assert.equal(form.createOutcomeUnknown.value, outcome === 'unknown')
      assert.equal(form.showAgentModal.value, true)
    } finally {
      for (const resolve of detailReads) resolve(detail)
      rejectCreation(Object.assign(new Error('invalid'), { status: 422 }))
      await Promise.allSettled([...opens, request])
    }
  })
}

test('独立 MCP 创建阻止父提交，成功选择后取消 Agent 不发送创建请求', async () => {
  const { form, writes } = await setupForm({})
  form.openCreate()
  await form.toggleCreateAdvanced()
  form.runtimeConfig.value = { mcps: ['ready'], system_prompt: '保留草稿' }
  form.openMcpCreate()
  assert.equal(form.mcpCreateOpen.value, true)
  await form.saveAgent()
  await form.closeAgentModal()
  assert.equal(form.showAgentModal.value, true)
  assert.deepEqual(writes, [])
  form.mcpCreateOpen.value = false
  await form.handleMcpCreated({ slug: 'new-server' })
  assert.deepEqual(form.runtimeConfig.value, { mcps: ['ready', 'new-server'], system_prompt: '保留草稿' })
  await form.closeAgentModal()
  assert.deepEqual(writes, [])
})

test('新资源不把全部模式缩成一个标识，共享 Skill 使用独立共享安装草稿', async () => {
  const { form } = await setupForm({})
  form.openCreate()
  await form.toggleCreateAdvanced()
  form.runtimeConfig.value = { mcps: 'all', skills: [] }
  await form.handleMcpCreated({ slug: 'new-server' })
  assert.equal(form.runtimeConfig.value.mcps, 'all')
  await form.beforeSharedSkillUpload({ name: 'shared.zip', size: 100 })
  assert.equal(form.sharedSkillOpen.value, true)
  assert.equal(form.sharedSkillFlow.value.drafts[0].draft_id, 'draft')
  await form.handleSharedSkillCreated({ slugs: ['shared-guide'] })
  assert.deepEqual(form.runtimeConfig.value.skills, ['shared-guide'])
  assert.equal(form.createSkillFile.value, null, '共享安装不占用专属包')
})

test('普通用户没有资源创建快捷入口，管理跳转不会丢失创建 Context', async () => {
  const { form, writes, navigations } = await setupForm({}, false)
  form.openCreate()
  assert.deepEqual(form.creatableResourceKinds.value, [])
  form.openMcpCreate()
  assert.equal(form.mcpCreateOpen.value, false)
  await form.beforeSharedSkillUpload({ name: 'shared.zip', size: 100 })
  assert.equal(form.sharedSkillOpen.value, false)
  form.runtimeConfig.value = { system_prompt: '创建草稿' }
  await form.manageRuntimeResource('skills')
  assert.deepEqual(form.runtimeConfig.value, { system_prompt: '创建草稿' })
  assert.deepEqual(navigations, [])
  assert.deepEqual(writes, [])
})

test('切换后端与重新打开后，旧 Schema 响应不能覆盖当前草稿', async () => {
  const { form, deps } = await setupForm({})
  const reads = []
  deps.agentApi.getAgentBackendDetail = (backend) => new Promise((resolve) => reads.push({ backend, resolve }))
  form.openCreate()
  const first = form.toggleCreateAdvanced()
  form.runtimeConfig.value = { system_prompt: '旧后端草稿' }
  form.agentForm.backend_id = 'AnotherTestBackend'
  await nextTick()
  assert.deepEqual(form.createContext.value, {})
  assert.equal(reads.length, 2)
  reads[1].resolve({ configurable_items: { current: { type: 'str' } } })
  await nextTick()
  form.runtimeConfig.value = { current: '保留' }
  reads[0].resolve({ configurable_items: { stale: { type: 'str' } } })
  await first
  assert.deepEqual(Object.keys(form.createConfigItems.value), ['current'])
  assert.deepEqual(form.createContext.value, { current: '保留' })
  const pending = form.loadCreateConfig()
  await form.closeAgentModal()
  assert.equal(form.createConfigLoading.value, false)
  form.openCreate()
  reads[2].resolve({ configurable_items: { closed: { type: 'str' } } })
  await pending
  assert.deepEqual(form.createConfigItems.value, {})
  assert.deepEqual(form.createContext.value, {})
})

test('专属 Skill mutation 等待时父级不关闭保存切换或打开，结束后恢复', async () => {
  const detail = { agent_id: 'bound-parent', name: '指南', backend_id: 'ChatbotAgent', visibility: 'private', can_manage: true }
  const { form, store, writes, navigations, routeGuards, deps } = await setupForm(detail)
  await form.openEdit(detail)
  form.agentModalActiveTab.value = 'skill'
  form.agentForm.name = '修改名称'
  let rejectUpload
  deps.agentApi.uploadAgentBoundSkill = () => new Promise((_, reject) => { rejectUpload = reject })
  const panel = await setupBoundPanel(form)
  const pending = panel.upload({ name: 'guide.zip' })
  assert.equal(form.boundSkillSaving.value, true)
  assert.equal(routeGuards[0](), false, '浏览器返回也不能卸载上传组件')
  await form.closeAgentModal()
  await form.saveAgent()
  form.selectAgentModalTab('basic')
  await form.openCreate()
  await form.openEdit({ agent_id: 'other' })
  await form.navigateToSkill({ name: 'ExtensionSkillDetail' })
  assert.equal(form.showAgentModal.value, true)
  assert.equal(form.editingAgentId.value, 'bound-parent')
  assert.equal(form.agentModalActiveTab.value, 'skill')
  assert.deepEqual(writes, [])
  assert.deepEqual(navigations, [])
  assert.equal(store.selectedAgentId, 'bound-parent')
  rejectUpload(new Error('替换失败'))
  await pending
  assert.equal(form.boundSkillSaving.value, false)
  assert.equal(routeGuards[0](), true)
  form.selectAgentModalTab('basic')
  assert.equal(form.agentModalActiveTab.value, 'basic')
  await form.saveAgent()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].payload.name, '修改名称')
  assert.equal(form.showAgentModal.value, false)
})

test('创建请求与独立资源忙碌时路由不能离开，结束后恢复', async () => {
  const { form, routeGuards } = await setupForm(null)
  await form.openCreate()
  for (const state of ['saving', 'agentIconUploading', 'mcpCreateOpen', 'sharedSkillPreparing', 'sharedSkillOpen']) {
    form[state].value = true
    assert.equal(routeGuards[0](), false, state)
    form[state].value = false
    assert.equal(routeGuards[0](), true, state)
  }
})
