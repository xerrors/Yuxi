import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, disposePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

test('保存配置采用服务端完整返回值，支持清空和重新选择默认模型', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  const pinia = createPinia()
  t.after(async () => { disposePinia(pinia); await server.close() })
  const { useConfigStore } = await server.ssrLoadModule('/src/modules/settings/model/config.js')
  const store = useConfigStore(pinia)
  store.config = { default_model: 'provider:old', stale_field: true }
  const { configApi } = await server.ssrLoadModule('/src/apis/system_api.js')
  t.mock.method(configApi, 'updateConfigBatch', async payload => ({ enable_rerank: true, ...payload }))
  await store.setConfigValue('default_model', '')
  assert.deepEqual(store.config, { default_model: '', enable_rerank: true })
  await store.setConfigValue('default_model', 'provider:new')
  assert.deepEqual(store.config, { default_model: 'provider:new', enable_rerank: true })
})
