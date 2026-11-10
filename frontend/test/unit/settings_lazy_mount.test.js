import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

test('设置惰性挂载并保留草稿，安全设置仅超管可见且拒绝越权初始页', async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [
      {
        name: 'settings-client-template',
        resolveId(id) {
          if (id === 'virtual:settings-test') return '\0' + id
        },
        load(id) {
          if (id.endsWith('/src/shared/lib/asyncPanel.js')) {
            return `import { h, ref } from 'vue'
              export const createAsyncPanel = (loader) => ({ setup() {
                const name = loader.toString().match(/ui\\/(\\w+)\\.vue/)[1]
                const draft = ref('')
                return () => h('input', { panel: name, value: draft.value,
                  onInput: (event) => { draft.value = event.target.value } })
              } })`
          }
          if (id !== '\0virtual:settings-test') return
          // 在内存中编译真实客户端模板，避免 SSR 模板跳过 v-show 等交互语义。
          const source = readFileSync(
            new URL('../../src/modules/settings/ui/SettingsModal.vue', import.meta.url),
            'utf8'
          )
          const { descriptor } = parse(source)
          return compileScript(descriptor, { id: 'settings-test', inlineTemplate: true }).content
        }
      }
    ]
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  let app
  try {
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    useUserStore().token = 'fixture'
    useUserStore().userRole = 'superadmin'
    const { default: Settings } = await server.ssrLoadModule('virtual:settings-test')
    const makeNode = (type) => ({ type, children: [], props: {}, style: {}, parent: null })
    const renderer = createRenderer({
      createElement: makeNode,
      createText: (text) => ({ ...makeNode('text'), text }),
      createComment: (text) => ({ ...makeNode('comment'), text }),
      insert(child, parent, anchor = null) {
        child.parent = parent
        const index = anchor ? parent.children.indexOf(anchor) : -1
        if (index < 0) parent.children.push(child)
        else parent.children.splice(index, 0, child)
      },
      remove(child) {
        child.parent.children.splice(child.parent.children.indexOf(child), 1)
      },
      setText(node, text) {
        node.text = text
      },
      setElementText(node, text) {
        node.text = text
        node.children = []
      },
      parentNode: (node) => node.parent,
      nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
      patchProp(node, key, _old, value) {
        node.props[key] = value
      }
    })
    const find = (node, predicate) =>
      predicate(node) ? node : node.children.map((child) => find(child, predicate)).find(Boolean)
    const host = makeNode('root')
    const visible = ref(true)
    const initialTab = ref('ocr')
    app = renderer.createApp(() => h(Settings, { visible: visible.value, initialTab: initialTab.value }))
    app.use(pinia)
    app.component('a-modal', {
      inheritAttrs: false,
      props: ['open'],
      emits: ['cancel', 'update:open'],
      setup:
        (props, { slots }) =>
        () =>
          props.open ? slots.default?.() : null
    })
    app.mount(host)
    assert.ok(find(host, (node) => node.props.panel === 'OCRSettingsSection'))
    assert.equal(
      find(host, (node) => node.props.panel === 'AccountSettingsComponent'),
      undefined
    )
    assert.equal(
      find(host, (node) => node.props.panel === 'BasicSettingsSection'),
      undefined
    )
    assert.equal(find(host, (node) => node.props.panel === 'SecuritySettingsSection'), undefined)
    assert.equal(
      find(host, (node) => node.props.panel === 'UserManagementComponent'),
      undefined
    )
    const ocr = find(host, (node) => node.props.panel === 'OCRSettingsSection')
    ocr.props.onInput({ target: { value: 'draft' } })
    const select = async (label) => {
      const button = find(
        host,
        (node) => node.props.onClick && node.children.some((child) => child.text === label)
      )
      assert.ok(button, label)
      button.props.onClick()
      await nextTick()
    }
    await select('账户设置')
    assert.ok(find(host, (node) => node.props.panel === 'AccountSettingsComponent'))
    await select('OCR 配置')
    assert.equal(
      find(host, (node) => node.props.panel === 'OCRSettingsSection'),
      ocr
    )
    assert.equal(ocr.props.value, 'draft')
    await select('安全设置')
    const security = find(host, (node) => node.props.panel === 'SecuritySettingsSection')
    assert.ok(security)
    security.props.onInput({ target: { value: 'security-draft' } })
    await select('OCR 配置')
    await select('安全设置')
    assert.equal(find(host, (node) => node.props.panel === 'SecuritySettingsSection'), security)
    assert.equal(security.props.value, 'security-draft')
    await select('API Keys')
    const apiKeys = find(host, (node) => node.props.panel === 'ApiKeyManagementComponent')
    apiKeys.props.onInput({ target: { value: 'api-draft' } })
    await select('账户设置')
    await select('API Keys')
    assert.equal(
      find(host, (node) => node.props.panel === 'ApiKeyManagementComponent'),
      apiKeys
    )
    assert.equal(apiKeys.props.value, 'api-draft')

    await select('运行环境')
    const agentEnv = find(host, (node) => node.props.panel === 'AgentEnvSettingsCard')
    agentEnv.props.onInput({ target: { value: 'env-draft' } })
    await select('账户设置')
    await select('运行环境')
    assert.equal(
      find(host, (node) => node.props.panel === 'AgentEnvSettingsCard'),
      agentEnv
    )
    assert.equal(agentEnv.props.value, 'env-draft')

    visible.value = false
    await nextTick()
    visible.value = true
    await nextTick()
    assert.ok(find(host, (node) => node.props.panel === 'OCRSettingsSection'))
    assert.equal(
      find(host, (node) => node.props.panel === 'AccountSettingsComponent'),
      undefined
    )
    for (const role of ['admin', 'user']) {
      visible.value = false
      await nextTick()
      useUserStore().userRole = role
      initialTab.value = 'security'
      visible.value = true
      await nextTick()
      assert.equal(find(host, (node) => node.props.panel === 'SecuritySettingsSection'), undefined)
      assert.equal(find(host, (node) => node.props.onClick && node.children.some((child) => child.text === '安全设置')), undefined)
      assert.ok(find(host, (node) => node.props.panel === 'AccountSettingsComponent'))
    }
  } finally {
    app?.unmount()
    disposePinia(pinia)
    await server.close()
  }
})
