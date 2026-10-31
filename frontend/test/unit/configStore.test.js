import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, disposePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

test('配置 store 接收保存后的服务端配置，支持清空和重新选择个人助理模型', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  const pinia = createPinia()
  t.after(async () => {
    disposePinia(pinia)
    await server.close()
  })
  const { useConfigStore } = await server.ssrLoadModule('/src/modules/settings/model/config.js')
  const store = useConfigStore(pinia)
  store.config = { personal_assistant_model: 'provider:old', default_model: 'provider:default' }

  store.setConfig({ personal_assistant_model: '', default_model: 'provider:default' })
  assert.equal(store.config.personal_assistant_model, '')
  assert.equal(store.config.default_model, 'provider:default')

  store.setConfig({ personal_assistant_model: 'provider:new', default_model: 'provider:default' })
  assert.equal(store.config.personal_assistant_model, 'provider:new')
})
