import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createPinia, setActivePinia, disposePinia } from 'pinia'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server, component
const pinia = createPinia()
const originalMessage = { error: message.error, success: message.success }
before(async () => {
  setActivePinia(pinia)
  server = await createTestServer({
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{ name: 'ocr-api-boundary', enforce: 'pre', load(id) {
      if (id.endsWith('/src/apis/system_api.js')) return `
        export const configOptionsApi = new Proxy({}, { get: (_, key) => globalThis.__ocrTestApi[key] })
        export const configApi = new Proxy({}, { get: (_, key) => globalThis.__ocrConfigApi?.[key] })
        export const ocrApi = { getOptions: async () => ({ option_keys: globalThis.__ocrOptionKeys || ['mineru_ocr_host_opts', 'mineru_official_api_opts'] }) }`
    } }]
  })
  ;({ default: component } = await server.ssrLoadModule('/src/modules/settings/ui/OCRSettingsSection.vue'))
})
after(async () => {
  await server?.close()
  disposePinia(pinia)
  Object.assign(message, originalMessage)
  delete globalThis.__ocrConfigApi
  delete globalThis.__ocrTestApi
})

/** 在真实组件中注入 API 并建立 Vue 上下文。 */
async function createForm(deps) {
  globalThis.__ocrTestApi = deps.configOptionsApi
  Object.assign(message, deps.message)
  let state
  const app = createSSRApp({ setup() {
    state = component.setup({}, { expose() {} })
    return () => h('div')
  } })
  app.use(pinia)
  await renderToString(app)
  return state
}

test('MinerU 档位显示默认值，保存选择并重载，取消与失败保留正确状态', async () => {
  const fields = [
    { key: 'server_url', environment: 'MINERU_API_URI' },
    { key: 'tier', default: 'standard', choice_labels: { standard: '标准（standard）', advanced: '高精度（advanced）' } }
  ]
  let saved = { key: 'mineru_ocr_host_opts', params: { fields }, value: { server_url: '' } }
  let failSave = false
  const errors = []
  const deps = {
    message: { success() {}, error: (value) => errors.push(value) },
    configOptionsApi: {
      getOptions: async () => ({ options: [structuredClone(saved)] }),
      updateOption: async (key, value) => {
        assert.equal(key, 'mineru_ocr_host_opts')
        if (failSave) throw new Error('保存失败')
        saved = { ...saved, value: { ...value } }
        return { option: structuredClone(saved) }
      }
    }
  }
  const form = await createForm(deps)
  await form.loadConfigOptions()
  let option = form.configOptions.value[0]
  assert.equal(form.getFieldDisplay(option, fields[1]), '标准（standard）')
  assert.equal(form.getFieldDisplay(option, fields[0]), '读取 MINERU_API_URI')
  form.startEditing(option)
  assert.deepEqual(form.draftValue.value, { server_url: '', tier: 'standard' })
  form.draftValue.value.tier = 'advanced'
  form.cancelEditing()
  assert.deepEqual(option.value, { server_url: '' })
  form.startEditing(option)
  form.draftValue.value.tier = 'advanced'
  await form.saveOption(option)
  assert.equal(form.editingKey.value, '')
  await form.loadConfigOptions()
  option = form.configOptions.value[0]
  assert.equal(form.getFieldDisplay(option, fields[1]), '高精度（advanced）')
  form.startEditing(option)
  form.draftValue.value.tier = 'standard'
  failSave = true
  await form.saveOption(option)
  assert.equal(form.editingKey.value, option.key)
  assert.equal(form.draftValue.value.tier, 'standard')
  assert.equal(option.value.tier, 'advanced')
  assert.equal(form.savingOption.value, '')
  assert.deepEqual(errors, ['保存失败'])
})

test('Official 默认模型兼容旧记录，修改模型保留凭据，清除凭据必须显式选择', async () => {
  const model = { key: 'model_version', default: 'pipeline', choice_labels: { pipeline: 'Pipeline（传统解析）', vlm: 'VLM（视觉模型解析）' } }
  const option = {
    key: 'mineru_official_api_opts',
    params: { fields: [{ key: 'api_key', sensitive: true }, model] },
    value: { api_key: '' }, sensitive_state: { api_key: { source: 'database' } }
  }
  let secret = 'fixture-original-key'
  const writes = []
  const form = await createForm({
    message: { success() {}, error: assert.fail },
    configOptionsApi: {
      getOptions: async () => ({ options: [structuredClone(option)] }),
      updateOption: async (key, value) => {
        assert.equal(key, option.key)
        writes.push({ ...value })
        if (Object.hasOwn(value, 'api_key')) secret = value.api_key
        option.value.model_version = value.model_version
        return { option: structuredClone(option) }
      }
    }
  })
  await form.loadConfigOptions()
  let current = form.configOptions.value[0]
  assert.equal(form.getFieldDisplay(current, model), 'Pipeline（传统解析）')
  form.startEditing(current)
  assert.equal(form.draftValue.value.model_version, 'pipeline')
  form.draftValue.value.model_version = 'vlm'
  await form.saveOption(current)
  assert.deepEqual(writes[0], { model_version: 'vlm' })
  assert.equal(secret, 'fixture-original-key')
  await form.loadConfigOptions()
  current = form.configOptions.value[0]
  assert.equal(form.getFieldDisplay(current, model), 'VLM（视觉模型解析）')
  form.startEditing(current)
  form.draftValue.value.api_key = '   '
  await form.saveOption(current)
  assert.deepEqual(writes[1], { model_version: 'vlm' })
  assert.equal(secret, 'fixture-original-key')
  form.startEditing(current)
  form.clearedFields.value.api_key = true
  await form.saveOption(current)
  assert.deepEqual(writes[2], { api_key: '', model_version: 'vlm' })
  assert.equal(secret, '')
  form.startEditing(current)
  assert.deepEqual(form.clearedFields.value, {})
  form.draftValue.value.api_key = 'fixture-new-key'
  await form.saveOption(current)
  assert.equal(secret, 'fixture-new-key')
})


test('默认 OCR 在请求成功前和失败后都保留持久值', async () => {
  const { useConfigStore } = await server.ssrLoadModule('/src/modules/settings/model/config.js')
  const store = useConfigStore(pinia)
  store.config = { default_ocr_engine: 'rapid_ocr' }
  let complete
  globalThis.__ocrConfigApi = { updateConfigBatch: () => new Promise(resolve => { complete = resolve }) }
  const pending = store.setConfigValue('default_ocr_engine', 'mineru_ocr')
  try {
    assert.equal(store.config.default_ocr_engine, 'rapid_ocr')
  } finally {
    complete({ default_ocr_engine: 'mineru_ocr' })
    await pending
  }
  assert.equal(store.config.default_ocr_engine, 'mineru_ocr')
  globalThis.__ocrConfigApi.updateConfigBatch = async () => { throw new Error('保存被拒绝') }
  await assert.rejects(store.setConfigValue('default_ocr_engine', 'deepseek_ocr'), /保存被拒绝/)
  assert.equal(store.config.default_ocr_engine, 'mineru_ocr')
})

test('新增服务配置由后端目录决定，无须前端硬编码 key', async () => {
  globalThis.__ocrOptionKeys = ['fixture_ocr_option']
  try {
    const form = await createForm({
      message: { success() {}, error: assert.fail },
      configOptionsApi: { getOptions: async () => ({ options: [
        { key: 'fixture_ocr_option', params: { fields: [] }, value: {} },
        { key: 'unrelated_option', params: { fields: [] }, value: {} }
      ] }) }
    })
    await form.loadConfigOptions()
    assert.deepEqual(form.configOptions.value.map(option => option.key), ['fixture_ocr_option'])
  } finally {
    delete globalThis.__ocrOptionKeys
  }
})
