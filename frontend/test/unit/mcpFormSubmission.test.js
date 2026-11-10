import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server, component
const originalMessage = { error: message.error, success: message.success }
before(async () => {
  server = await createTestServer({
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{ name: 'mcp-api-boundary', enforce: 'pre', load(id) {
      if (id.endsWith('/src/apis/mcp_api.js')) return 'export const mcpApi = new Proxy({}, { get: (_, key) => globalThis.__mcpFormApi[key] })'
    } }]
  })
  ;({ default: component } = await server.ssrLoadModule('/src/modules/extensions/ui/McpFormModal.vue'))
})
after(async () => {
  await server?.close()
  Object.assign(message, originalMessage)
  delete globalThis.__mcpFormApi
})

/** 在真实组件中注入接口并建立 Vue 上下文。 */
async function setupForm(deps, props, context, interact) {
  globalThis.__mcpFormApi = deps.mcpApi
  Object.assign(message, deps.message)
  let state
  await renderToString(createSSRApp({ setup() {
    state = component.setup(props, context)
    interact?.(state)
    return () => h('div')
  } }))
  return state
}

for (const editMode of [false, true]) {
  for (const headersText of ['false', '0', 'null', '[]']) {
    test(`${editMode ? '编辑' : '创建'}表单拒绝显式非法 headers ${headersText}`, async () => {
      const errors = []
      const writes = []
      const events = []
      const deps = {
        message: { error: (value) => errors.push(value) },
        mcpApi: {
          createMcpServer: (data) => writes.push(data),
          updateMcpServer: (slug, data) => writes.push({ slug, ...data })
        }
      }
      const form = await setupForm(deps, { open: true, editMode, editData: null }, {
        expose() {}, emit: (...args) => events.push(args)
      })
      Object.assign(form.form, {
        slug: 'example', name: 'Example', url: 'https://example.com/mcp', headersText, auth_mode: 'headers'
      })
      await form.handleFormSubmit()
      assert.match(errors[0], /headers 必须是字符串键值对象/)
      assert.deepEqual(writes, [])
      assert.deepEqual(events, [])
      assert.equal(form.formLoading.value, false)
    })
  }
}


test('编辑共享认证并清空请求头时，显式提交空 headers 以允许切换 OAuth', async () => {
  const writes = []
  const deps = {
    message: { error: (value) => { throw new Error(value) }, success() {} },
    mcpApi: { updateMcpServer: async (slug, data) => { writes.push({ slug, ...data }); return { success: true, data } } }
  }
  const form = await setupForm(deps, { open: true, editMode: true, editData: { slug: 'existing', name: 'Existing', url: 'https://mcp.example/mcp', headers: { Authorization: 'Bearer shared' } } }, { expose() {}, emit() {} })
  Object.assign(form.form, { headersText: '', auth_mode: 'oauth', issuer_url: 'https://sso.example', client_id: 'client' })
  await form.handleFormSubmit()
  assert.deepEqual(writes[0].headers, {})
  assert.equal(writes[0].auth_mode, 'oauth')
  assert.equal(writes[0].oauth_config.client_id, 'client')
  assert.equal(Object.hasOwn(writes[0].oauth_config, 'require_same_identity'), false)
})

for (const publicHeaders of [null, { 'X-Version': '2' }]) {
  test(`共享认证切换为无需认证${publicHeaders ? '后可填写公共协议头' : '时清除旧共享凭证'}`, async () => {
    const writes = []
    const form = await setupForm({
      message: { error: assert.fail, success() {} },
      mcpApi: { updateMcpServer: async (slug, data) => { writes.push(data); return { success: true, data } } }
    }, { open: true, editMode: true, editData: {
      slug: 'shared', name: 'Shared', url: 'https://example.com/mcp', auth_mode: 'headers',
      headers: { 'X-API-Key': 'fixture-shared-key' }
    } }, { expose() {}, emit() {} }, ({ form }) => {
      form.auth_mode = 'none'
      if (publicHeaders) form.headersText = JSON.stringify(publicHeaders)
    })
    await form.handleFormSubmit()
    assert.equal(writes[0].auth_mode, 'none')
    assert.deepEqual(writes[0].headers, publicHeaders || {})
    assert.equal(JSON.stringify(writes[0]).includes('fixture-shared-key'), false)
  })
}


for (const [label, clearSecret, clientSecret, expectedSecret] of [
  ['保留已有值', false, '', undefined],
  ['显式清除', true, '', ''],
  ['替换已有值', false, 'replacement-secret', 'replacement-secret'],
  ['清除优先于新输入', true, 'replacement-secret', '']
]) {
  test(`编辑 OAuth Client Secret：${label}`, async () => {
    const writes = []
    const deps = {
      message: { error: (value) => { throw new Error(value) }, success() {} },
      mcpApi: { updateMcpServer: async (slug, data) => { writes.push(data); return { success: true, data } } }
    }
    const form = await setupForm(deps, { open: true, editMode: true, editData: {
      slug: 'oauth', name: 'OAuth', url: 'https://mcp.example/mcp', auth_mode: 'oauth',
      oauth_client_secret_set: true, oauth_config: { issuer_url: 'https://sso.example', client_id: 'client' }
    } }, { expose() {}, emit() {} })
    form.form.clear_client_secret = clearSecret
    form.form.client_secret = clientSecret
    await form.handleFormSubmit()
    assert.equal(Object.hasOwn(writes[0].oauth_config, 'client_secret'), expectedSecret !== undefined)
    assert.equal(writes[0].oauth_config.client_secret, expectedSecret)
  })
}

for (const mode of ['none', 'bearer', 'api_key']) {
  test(`创建 ${mode} 服务只提交该方式的配置`, async () => {
    const writes = []
    const { form, handleFormSubmit } = await setupForm({
      message: { error: assert.fail, success() {} },
      mcpApi: { createMcpServer: async (data) => { writes.push(data); return { success: true, data } } }
    }, { open: true, editMode: false, editData: null }, { expose() {}, emit() {} })
    Object.assign(form, { slug: 'personal', name: 'Personal', url: 'https://example.com/mcp', auth_mode: mode, client_secret: 'hidden-oauth-secret' })
    await handleFormSubmit()
    assert.equal(writes[0].auth_mode, mode)
    assert.equal(writes[0].api_key_header, mode === 'api_key' ? 'X-API-Key' : undefined)
    assert.equal(Object.hasOwn(writes[0], 'oauth_config'), false)
  })
}
