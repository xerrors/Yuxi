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
    plugins: [{ name: 'url-import-settings-api', enforce: 'pre', load(id) {
      if (id.endsWith('/src/apis/system_api.js')) return `
        export const configOptionsApi = new Proxy({}, { get: (_, key) => globalThis.__urlImportSettingsApi[key] })`
    } }]
  })
  ;({ default: component } = await server.ssrLoadModule('/src/modules/knowledge/ui/UrlImportSettingsSection.vue'))
})
after(async () => {
  await server?.close()
  Object.assign(message, originalMessage)
  delete globalThis.__urlImportSettingsApi
})

/** 在真实组件建立 Vue 上下文，网络与通知由用例控制。 */
async function createForm(api, errors = []) {
  globalThis.__urlImportSettingsApi = api
  Object.assign(message, { success() {}, error: value => errors.push(value) })
  let state
  const app = createSSRApp({ setup() {
    state = component.setup({}, { expose() {} })
    return () => h('div')
  } })
  await renderToString(app)
  return state
}

test('URL 导入表单展示部署有效值，保存失败保留草稿，明确空列表重载仍关闭', async () => {
  let stored = {}
  let failSave = true
  const errors = []
  const response = () => ({ key: 'knowledge_url_import_policy', value: structuredClone(stored),
    effective_value: { allowed_hosts: stored.allowed_hosts ?? ['legacy.example'] } })
  const form = await createForm({
    getOptions: async () => ({ options: [response()] }),
    updateOption: async (key, value) => {
      assert.equal(key, 'knowledge_url_import_policy')
      if (failSave) throw new Error('保存失败，请重试')
      stored = { allowed_hosts: [...value.allowed_hosts] }
      return { option: response() }
    }
  }, errors)
  await form.loadOption()
  assert.deepEqual(form.hosts.value, ['legacy.example'])
  form.startEditing()
  form.draftHosts.value.push('docs.example.com')
  assert.deepEqual(form.hosts.value, ['legacy.example'])
  await form.saveOption()
  assert.equal(form.editing.value, true)
  assert.equal(form.saving.value, false)
  assert.deepEqual(form.draftHosts.value, ['legacy.example', 'docs.example.com'])
  assert.deepEqual(stored, {})
  assert.deepEqual(errors, ['保存失败，请重试'])

  failSave = false
  await form.saveOption()
  assert.deepEqual(form.hosts.value, ['legacy.example', 'docs.example.com'])
  assert.equal(form.editing.value, false)
  form.startEditing()
  form.draftHosts.value = []
  await form.saveOption()
  await form.loadOption()
  assert.deepEqual(stored, { allowed_hosts: [] })
  assert.deepEqual(form.hosts.value, [])
})

test('加载失败和缺少配置时展示错误，可重新加载恢复', async () => {
  let failure = 'network'
  const form = await createForm({ getOptions: async () => {
    if (failure === 'network') throw new Error('网络不可用')
    return { options: failure === 'missing' ? [] : [{ key: 'knowledge_url_import_policy', effective_value: { allowed_hosts: [] } }] }
  } })
  await form.loadOption()
  assert.equal(form.loading.value, false)
  assert.equal(form.loadError.value, '网络不可用')
  failure = 'missing'
  await form.loadOption()
  assert.match(form.loadError.value, /配置暂不可用/)
  failure = ''
  await form.loadOption()
  assert.equal(form.loadError.value, '')
  assert.deepEqual(form.hosts.value, [])
})
