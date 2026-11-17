import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultProvider, keyState, testOutcome, testPayload, webProviders } from '../../src/modules/settings/model/webSearchSettings.js'

const optionRecords = [
  { key: 'tavily_search_opts', name: 'Tavily', params: { provider: { id: 'tavily', supports_fetch: true, environment: 'TAVILY_API_KEY' } } },
  { key: 'deepseek_search_opts', name: 'DeepSeek', params: { provider: { id: 'deepseek', supports_fetch: true, environment: 'DEEPSEEK_API_KEY' } } },
  { key: 'doubao_search_opts', name: '豆包 Custom', params: { provider: { id: 'doubao', supports_fetch: false, environment: 'DOUBAO_SEARCH_API_KEY' } } },
  { key: 'unrelated', name: '无关配置', params: { fields: [] } }
]

test('三个独立 Provider 从配置元数据派生顺序，豆包不能用于 Fetch', () => {
  const providers = webProviders(optionRecords)
  assert.deepEqual(providers.map(({ id }) => id), ['tavily', 'deepseek', 'doubao'])
  assert.equal(providers.find(({ id }) => id === 'doubao').fetch, false)
  assert.equal(providers.find(({ id }) => id === 'tavily').environment, 'TAVILY_API_KEY')
  const configured = Object.fromEntries(providers.map(({ id }) => [id, { sensitive_state: { api_key: { configured: true } } }]))
  assert.equal(defaultProvider('search', {}, providers, configured), 'tavily')
  assert.equal(defaultProvider('fetch', {}, providers, { doubao: configured.doubao }), undefined)
  assert.equal(defaultProvider('search', { web_search_provider: 'deepseek' }, providers, configured), 'deepseek')
  assert.equal(defaultProvider('search', { web_search_provider: ' TAVILY ' }, providers, configured), 'tavily')
  assert.equal(defaultProvider('fetch', {}, providers, { deepseek: configured.deepseek }), 'deepseek')
})

test('固定测试使用内置查询和文档，空草稿不传 Key', () => {
  assert.equal(keyState(undefined).configured, false)
  assert.deepEqual(testPayload('doubao', 'search'), { provider: 'doubao', query: 'Python 官方文档', count: 1 })
  assert.deepEqual(testPayload('deepseek', 'fetch', ' draft '), {
    provider: 'deepseek', url: 'https://docs.python.org/3/tutorial/index.html', api_key: 'draft'
  })
  assert.equal('api_key' in testPayload('tavily', 'search', '  '), false)
})

test('测试仅在取得搜索结果或非空正文后通过', () => {
  assert.deepEqual(testOutcome('search', { results: [{ url: 'https://example.com' }] }), { state: 'passed' })
  assert.deepEqual(testOutcome('fetch', { content: '# Body' }), { state: 'passed' })
  assert.equal(testOutcome('search', { results: [] }).state, 'failed')
  assert.equal(testOutcome('fetch', { content: '  ' }).state, 'failed')
})

test('凭据表单空白保存保留原值，只有显式清除才发送空值，失败保留编辑内容', async (t) => {
  const { createTestServer } = await import('../helpers/createTestServer.js')
  const { createRenderer, h, ssrContextKey, toRaw } = await import('vue')
  const server = await createTestServer({ server: { middlewareMode: true }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'web-credential-feedback', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue') return '\0web-credential-feedback' },
    load(id) { if (id === '\0web-credential-feedback') return 'export const message = { info() {}, success() {}, error() {} }' }
  }] })
  const { configOptionsApi, configApi } = await server.ssrLoadModule('/src/apis/system_api.js')
  t.mock.method(configOptionsApi, 'getOptions', async () => ({ options: optionRecords }))
  t.mock.method(configApi, 'getConfig', async () => ({}))
  const persisted = { api_key: 'fixture-existing' }
  const payloads = []
  t.mock.method(configOptionsApi, 'updateOption', async (key, value) => {
    assert.equal(key, 'tavily_search_opts')
    payloads.push(value)
    Object.assign(persisted, value)
    return { option: { sensitive_state: { api_key: { configured: Boolean(persisted.api_key) } } } }
  })
  const component = (await server.ssrLoadModule('/src/modules/settings/ui/WebSearchSettingsSection.vue')).default
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  const app = renderer.createApp({ ...component, render: () => h('div') })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount({})
  t.after(async () => { app.unmount(); await server.close() })
  const s = toRaw(app._instance.setupState)
  const provider = webProviders(optionRecords)[0]
  s.startEditing(provider)
  s.draftKey.value = '  '
  await s.saveKey(provider)
  assert.equal(persisted.api_key, 'fixture-existing')
  assert.deepEqual(payloads, [])

  s.startEditing(provider)
  s.clearKey.value = true
  assert.equal(s.testDisabled(provider), true)
  await s.saveKey(provider)
  assert.deepEqual(payloads, [{ api_key: '' }])
  assert.equal(persisted.api_key, '')

  s.startEditing(provider)
  assert.equal(s.clearKey.value, false)
  s.draftKey.value = ' fixture-new '
  await s.saveKey(provider)
  assert.equal(persisted.api_key, 'fixture-new')
  s.startEditing(provider)
  s.clearKey.value = true
  s.cancelEditing()
  assert.equal(persisted.api_key, 'fixture-new', '取消清除不能改变已保存配置')

  t.mock.method(configOptionsApi, 'updateOption', async () => { throw new Error('服务暂不可用') })
  s.startEditing(provider)
  s.draftKey.value = 'fixture-retry'
  await s.saveKey(provider)
  assert.equal(persisted.api_key, 'fixture-new')
  assert.equal(s.editingKey.value, provider.id)
  assert.equal(s.draftKey.value, 'fixture-retry')
})
