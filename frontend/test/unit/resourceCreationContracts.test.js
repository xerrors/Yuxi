import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createPinia, setActivePinia, disposePinia } from 'pinia'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server
const pinia = createPinia()
const originalMessage = { error: message.error, success: message.success }
const previousStorage = globalThis.localStorage
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  setActivePinia(pinia)
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{ name: 'resource-api-boundaries', enforce: 'pre', load(id) {
      if (id.endsWith('/src/apis/skill_api.js')) return 'export const skillApi = new Proxy({}, { get: (_, key) => globalThis.__resourceTest.skillApi?.[key] })'
      if (id.endsWith('/src/apis/mcp_api.js')) return 'export const mcpApi = new Proxy({}, { get: (_, key) => globalThis.__resourceTest.mcpApi?.[key] })'
    } }]
  })
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  useUserStore(pinia).userRole = 'admin'
})
after(async () => {
  await server?.close()
  disposePinia(pinia)
  Object.assign(message, originalMessage)
  delete globalThis.__resourceTest
  if (previousStorage === undefined) delete globalThis.localStorage
  else globalThis.localStorage = previousStorage
})

/** 在真实资源弹窗中替换网络边界。 */
async function setupModal(file, props, dependencies) {
  globalThis.__resourceTest = dependencies
  if (dependencies.message) Object.assign(message, dependencies.message)
  const { default: component } = await server.ssrLoadModule(`/src/modules/extensions/ui/${file}.vue`)
  const events = []
  let modal
  const app = createSSRApp({ setup() {
    modal = component.setup(props, { expose() {}, emit: (...args) => events.push(args) })
    return () => h('div')
  } })
  app.use(pinia)
  await renderToString(app)
  return { modal, events }
}

test('固定共享入口发布到共享 API，完成返回实际安装 slug，默认入口仍是个人', async () => {
  const calls = []
  const dependencies = {
    skillApi: {
      confirmPersonalSkillInstallDraft: () => assert.fail('共享入口不能发布到个人目录'),
      confirmSkillInstallDraft: async (draftId, share, slugs) => {
        calls.push({ draftId, share, slugs })
        return { data: [{ success: true, requested_slug: 'guide', slug: 'installed-guide' }] }
      },
      discardSkillInstallDraft: () => assert.fail('成功草稿已经消费')
    },
  }
  const flow = { kind: 'draft', drafts: [{ draft_id: 'draft', items: [{ slug: 'guide', success: true }],
    default_share_config: { version: 2, read_scope: { access_level: 'global' } }, allowed_access_levels: ['global'] }] }
  const { modal, events } = await setupModal('SkillInstallFlowModal', { open: true, flow, target: 'shared' }, dependencies)
  assert.equal(modal.installTarget.value, 'shared')
  await modal.installDrafts()
  await modal.finishFlow()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].draftId, 'draft')
  assert.deepEqual(calls[0].slugs, ['guide'])
  assert.equal(calls[0].share.read_scope.access_level, 'global')
  assert.deepEqual(events, [['completed', { success: 1, failed: 0, slugs: ['installed-guide'] }], ['close']])
  const { modal: defaultModal } = await setupModal('SkillInstallFlowModal', { open: true, flow, target: '' }, dependencies)
  assert.equal(defaultModal.installTarget.value, 'personal')
})

test('独立 MCP 发布等待时禁止重复提交与关闭，成功才返回资源标识', async () => {
  let resolveCreate
  const calls = []
  const { modal, events } = await setupModal('McpFormModal', { open: true, editMode: false, editData: null }, {
    message: { success() {}, error: (message) => assert.fail(message) },
    mcpApi: { createMcpServer: (data) => { calls.push(data); return new Promise((resolve) => { resolveCreate = resolve }) } }
  })
  Object.assign(modal.form, { slug: 'query', name: '查询', url: 'https://example.com/mcp', auth_mode: 'none' })
  const request = modal.handleFormSubmit()
  await modal.handleFormSubmit()
  modal.visible.value = false
  assert.equal(modal.formLoading.value, true)
  assert.equal(calls.length, 1)
  assert.deepEqual(events, [])
  assert.equal(calls[0].slug, 'query')
  resolveCreate({ success: true, data: { slug: 'query', enabled: true } })
  await request
  assert.equal(modal.formLoading.value, false)
  assert.deepEqual(events, [['update:open', false], ['submitted', { slug: 'query', enabled: true }]])
})
