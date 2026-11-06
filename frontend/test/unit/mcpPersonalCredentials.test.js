import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'
import { mcpConnectionLabel } from '../../src/modules/extensions/model/mcpOAuth.js'

let server, component
before(async () => {
  server = await createTestServer({
    server: { middlewareMode: true }, appType: 'custom',
    plugins: [{ name: 'personal-mcp-api', enforce: 'pre', load(id) {
      if (id.endsWith('/src/apis/mcp_api.js')) return 'export const mcpApi = new Proxy({}, { get: (_, key) => globalThis.__personalMcpApi[key] })'
    } }]
  })
  ;({ default: component } = await server.ssrLoadModule('/src/modules/extensions/ui/McpConnectionControls.vue'))
})
after(async () => {
  await server?.close()
  delete globalThis.__personalMcpApi
})

/** 装配完整生产组件，外部接口在 HTTP 客户端边界替换。 */
async function setupControls(mode, api) {
  globalThis.__personalMcpApi = api
  let state
  const events = []
  await renderToString(createSSRApp({ setup() {
    state = component.setup({ server: { slug: 'personal', auth_mode: mode, enabled: true, connection_status: 'connected', config_revision: 'revision', api_key_header: 'X-API-Key' } }, { expose() {}, emit: (...args) => events.push(args) })
    return () => h('div')
  } }))
  return { state, events }
}

for (const mode of ['bearer', 'api_key']) {
  test(`${mode} 保存只提交本人秘密与修订，不自动测试或回填`, async () => {
    const writes = []
    const { state, events } = await setupControls(mode, {
      saveCredentials: async (...args) => writes.push(args),
      testConnection: () => assert.fail('保存不应自动测试')
    })
    assert.equal(state.secret.value, '')
    assert.equal(mcpConnectionLabel('connected', mode), '已配置')
    state.secret.value = 'fixture-personal-secret'
    await state.save()
    assert.deepEqual(writes, [['personal', 'fixture-personal-secret', 'revision']])
    assert.equal(state.secret.value, '')
    assert.equal(state.result.value, '')
    assert.deepEqual(events, [['changed']])
  })
}

test('保存失败保留草稿，取消替换不发送清除请求', async () => {
  const { state, events } = await setupControls('bearer', {
    saveCredentials: async () => { throw new Error('保存失败') },
    disconnect: () => assert.fail('取消不能清除已保存值')
  })
  state.secret.value = 'retry-secret'
  await state.save()
  assert.equal(state.error.value, '保存失败')
  assert.equal(state.secret.value, 'retry-secret')
  assert.deepEqual(events, [])
  state.cancelEdit()
  assert.equal(state.secret.value, '')
})
