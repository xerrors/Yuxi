import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers/promises'
import test from 'node:test'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, ssrContextKey } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 保留真实数字控件的宿主属性和键盘事件。 */
const node = (type) => ({ type, children: [], props: {}, style: {}, parent: null, focus() {}, blur() {} })
const renderer = createRenderer({
  createElement: node, createText: node, createComment: node,
  insert(child, parent, anchor) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) { child.parent.children.splice(child.parent.children.indexOf(child), 1) },
  setText() {}, setElementText() {}, parentNode: (item) => item.parent,
  nextSibling: (item) => item.parent?.children[item.parent.children.indexOf(item) + 1] || null,
  patchProp(item, key, _old, value) { item.props[key] = value }
})

/** 按真实 input id 查找控件，不访问组件私有状态。 */
function find(root, predicate) {
  if (predicate(root)) return root
  return root.children.map((child) => find(child, predicate)).find(Boolean)
}

test('基本设置保存期间禁用真实数字输入，成功或失败后恢复可编辑且只发布已保存值', async () => {
  const originalStorage = globalThis.localStorage
  const originalError = message.error
  const errors = [], requests = [], pending = []
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  message.error = (text) => errors.push(text)
  globalThis.__basicSettingsApi = {
    updateConfigBatch: (payload) => {
      requests.push(payload)
      return new Promise((resolve, reject) => pending.push({ resolve, reject }))
    }
  }
  const server = await createTestServer({
    server: { middlewareMode: true },
    plugins: [{
      name: 'basic-settings-client-template', enforce: 'pre',
      resolveId(id) { if (id === 'virtual:basic-settings-save') return '\0' + id },
      load(id) {
        if (id === '\0virtual:basic-settings-save') {
          const source = readFileSync(new URL('../../src/modules/settings/ui/BasicSettingsSection.vue', import.meta.url), 'utf8')
          return compileScript(parse(source).descriptor, { id, inlineTemplate: true }).content
        }
        if (id.endsWith('/src/apis/system_api.js')) return 'export const configApi = globalThis.__basicSettingsApi'
        if (id.endsWith('.vue')) return '<script>export default { render() { return null } }</script>'
      }
    }]
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  let app
  try {
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    useUserStore(pinia).userRole = 'superadmin'
    const { useConfigStore } = await server.ssrLoadModule('/src/modules/settings/model/config.js')
    const store = useConfigStore(pinia)
    store.config = {}
    const { default: Settings } = await server.ssrLoadModule('virtual:basic-settings-save')
    // Ant Design Vue 使用的真实受控输入实现，保留其步进与 disabled 行为。
    const { default: InputNumber } = await server.ssrLoadModule('/node_modules/ant-design-vue/es/input-number/src/InputNumber.js')
    app = renderer.createApp(() => h(Settings))
    app.use(pinia)
    app.provide(ssrContextKey, { modules: new Set() })
    app.component('a-input-number', InputNumber)
    app.component('a-spin', {
      inheritAttrs: false,
      props: ['spinning', 'wrapperClassName'],
      setup: (props, { slots }) => () => h('div', { class: props.wrapperClassName, 'data-saving': props.spinning }, slots.default?.())
    })
    const host = node('root')
    app.mount(host)
    assert.match(host.children[0].props.class || '', /\bsettings-page\b/, 'Spin 外层必须保留设置页布局容器')
    const input = (id) => find(host, (item) => item.type === 'input' && item.props.id === id)
    const depth = () => input('max-cooperation-depth')
    const concurrency = () => input('max-cooperation-concurrency')
    const increase = (control) => control.parent.parent.props.onKeydown({ which: 38, preventDefault() {} })
    const settle = async () => { await setImmediate(); await nextTick() }

    assert.equal(depth().props.value, '1')
    assert.equal(concurrency().props.value, '12')
    assert.equal(concurrency().props['aria-valuemax'], 20)
    store.config = { max_cooperation_depth: 2, max_cooperation_concurrency: 4 }
    await nextTick()

    increase(depth())
    // disabled 尚未投影到控件时，第二个真实事件也必须被保存入口拒绝。
    increase(concurrency())
    await settle()
    assert.deepEqual(requests, [{ max_cooperation_depth: 3 }])
    assert.equal(depth().props.disabled, true)
    assert.equal(concurrency().props.disabled, true)
    assert.equal(find(host, (item) => item.props['data-saving'] === true)?.props['data-saving'], true)
    assert.equal(store.config.max_cooperation_depth, 2)
    increase(depth())
    increase(concurrency())
    await settle()
    assert.equal(requests.length, 1)

    pending.shift().resolve({ max_cooperation_depth: 3, max_cooperation_concurrency: 4 })
    await settle()
    assert.equal(depth().props.disabled, false)
    assert.equal(concurrency().props.disabled, false)
    assert.equal(depth().props.value, '3')
    increase(depth())
    await settle()
    assert.deepEqual(requests[1], { max_cooperation_depth: 4 })
    pending.shift().reject(new Error('保存失败'))
    await settle()
    assert.deepEqual(errors, ['保存失败'])
    assert.equal(store.config.max_cooperation_depth, 3)
    assert.equal(depth().props.value, '3')
    assert.equal(depth().props.disabled, false)
    assert.equal(concurrency().props.disabled, false)
    increase(concurrency())
    await settle()
    assert.deepEqual(requests[2], { max_cooperation_concurrency: 5 })
    pending.shift().resolve({ max_cooperation_depth: 3, max_cooperation_concurrency: 5 })
    await settle()
    assert.equal(concurrency().props.value, '5')
  } finally {
    app?.unmount()
    disposePinia(pinia)
    await server.close()
    message.error = originalError
    if (originalStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = originalStorage
    delete globalThis.__basicSettingsApi
  }
})
