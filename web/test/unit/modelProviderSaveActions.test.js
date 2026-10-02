import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import { setImmediate } from 'node:timers'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, nextTick } from 'vue'

const componentPath = fileURLToPath(
  new URL('../../src/components/model-management/ModelProviderManagePanel.vue', import.meta.url)
)
const compiledPath = fileURLToPath(
  new URL(`../../.model-provider-actions-test-${pid}.mjs`, import.meta.url)
)
const source = readFileSync(componentPath, 'utf8')
const requests = []
const confirmations = []
const notices = []
const configStore = { config: { default_model: 'other:model' }, refreshConfig: async () => {} }
let updateProvider = async () => ({})

globalThis.__modelProviderActionsTestDeps = {
  message: {
    success: (value) => notices.push(['success', value]),
    error: (value) => notices.push(['error', value]),
    warning: (value) => notices.push(['warning', value])
  },
  Modal: { confirm: (options) => confirmations.push(options) },
  useConfigStore: () => configStore,
  modelProviderApi: {
    getProviders: async () => ({ data: [provider] }),
    updateProvider: (id, payload) => {
      requests.push([id, payload])
      return updateProvider(id, payload)
    }
  },
  TextInitial: null,
  Image: null,
  Video: null,
  AudioLines: null,
  FileText: null
}

const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'model-provider-actions-test' }).content
const executable = compiled
  .replace(/^import(?:\s*\{[\s\S]*?\}|\s+[A-Za-z]\w*)\s+from\s+'[^']+'\n/gm, (line) =>
    line.includes("from 'vue'") ? line : ''
  )
  .replace(
    /const __returned__ = \{[\s\S]*?\}\nObject\.defineProperty/,
    'const __returned__ = { showProviderModal, originalProviderEnabled, providerForm, saving, openCreateProviderModal, openEditProviderModal, toggleProviderEnabled, deleteProviderFromEdit }\nObject.defineProperty'
  )
  .replace(
    "import { computed, onMounted, reactive, ref } from 'vue'",
    "import { computed, onMounted, reactive, ref } from 'vue'\nconst { message, Modal, useConfigStore, modelProviderApi, TextInitial, Image, Video, AudioLines, FileText } = globalThis.__modelProviderActionsTestDeps"
  )
writeFileSync(compiledPath, executable)
let ProviderPanel
try {
  ;({ default: ProviderPanel } = await import(pathToFileURL(compiledPath).href))
} finally {
  unlinkSync(compiledPath)
}
ProviderPanel.render = () => null

const makeNode = (type) => ({ type, children: [], props: {}, parent: null, text: '' })
const renderer = createRenderer({
  createElement: makeNode,
  createText: (value) => ({ ...makeNode('text'), text: value }),
  createComment: (value) => ({ ...makeNode('comment'), text: value }),
  insert(child, parent) {
    child.parent = parent
    parent.children.push(child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText(node, value) {
    node.text = value
  },
  setElementText(node, value) {
    node.text = value
    node.children = []
  },
  parentNode: (node) => node.parent,
  nextSibling: () => null,
  patchProp(node, key, _previous, value) {
    node.props[key] = value
  }
})

function mountPanel() {
  requests.length = 0
  confirmations.length = 0
  notices.length = 0
  updateProvider = async () => ({})
  configStore.config.default_model = 'other:model'
  const app = renderer.createApp(ProviderPanel)
  app.mount(makeNode('root'))
  return { panel: app._instance.setupState, unmount: () => app.unmount() }
}

const provider = { provider_id: 'sample', display_name: 'Sample', is_enabled: true }
const flushAsync = () => new Promise((resolve) => setImmediate(resolve))

test('供应商弹窗以标题栏开关替代关闭叉号和保存并启用', () => {
  const modal = source.slice(
    source.indexOf('<!-- Provider Edit Modal -->'),
    source.indexOf('</a-modal>', source.indexOf('<!-- Provider Edit Modal -->'))
  )
  const title = modal.slice(modal.indexOf('<template #title>'), modal.indexOf('</template>'))
  const footerActions = modal.slice(
    modal.indexOf('<div class="provider-modal-footer-actions">'),
    modal.indexOf('</div>', modal.indexOf('<div class="provider-modal-footer-actions">'))
  )

  assert.match(modal, /:closable="false"/)
  assert.match(title, /<a-switch[\s\S]*@change="toggleProviderEnabled"/)
  assert.match(modal, /class="modal-form"[^>]*:inert="saving"/)
  assert.match(modal, /:disabled="saving"[^>]*@click="deleteProviderFromEdit"/)
  assert.doesNotMatch(modal, /<span>状态<\/span>|仅保存|保存并启用/)
  assert.equal((footerActions.match(/<a-button/g) || []).length, 2)
})

test('停用只提交启用状态，成功关闭；启用成功保持打开', async () => {
  const { panel, unmount } = mountPanel()
  try {
    panel.openEditProviderModal(provider)
    panel.toggleProviderEnabled(false)
    await flushAsync()
    assert.deepEqual(requests, [['sample', { is_enabled: false }]])
    assert.equal(panel.showProviderModal, false)
    assert.equal(panel.originalProviderEnabled, false)

    panel.openEditProviderModal({ ...provider, is_enabled: false })
    panel.toggleProviderEnabled(true)
    await flushAsync()
    assert.deepEqual(requests[1], ['sample', { is_enabled: true }])
    assert.equal(panel.showProviderModal, true)
    assert.equal(panel.originalProviderEnabled, true)
  } finally {
    unmount()
  }
})

test('状态更新失败时保留弹窗和原启用状态', async () => {
  const { panel, unmount } = mountPanel()
  try {
    updateProvider = async () => {
      throw new Error('网络失败')
    }
    panel.openEditProviderModal(provider)
    panel.toggleProviderEnabled(false)
    await nextTick()
    await nextTick()
    assert.deepEqual(requests, [['sample', { is_enabled: false }]])
    assert.equal(panel.showProviderModal, true)
    assert.equal(panel.originalProviderEnabled, true)
    assert.deepEqual(notices.at(-1), ['error', '网络失败'])
  } finally {
    unmount()
  }
})

test('有未保存字段时先确认，取消不提交，确认后仅停用', async () => {
  const { panel, unmount } = mountPanel()
  try {
    panel.openEditProviderModal(provider)
    panel.providerForm.display_name = '未保存的新名称'
    panel.toggleProviderEnabled(false)
    assert.equal(confirmations.length, 1)
    assert.deepEqual(requests, [])
    assert.equal(panel.showProviderModal, true)
    assert.equal(panel.providerForm.display_name, '未保存的新名称')

    confirmations.pop() // 关闭确认框，相当于点击“继续编辑”。
    assert.deepEqual(requests, [])
    assert.equal(panel.showProviderModal, true)
    assert.equal(panel.providerForm.display_name, '未保存的新名称')

    panel.toggleProviderEnabled(false)
    await confirmations[0].onOk()
    assert.deepEqual(requests, [['sample', { is_enabled: false }]])
    assert.equal(panel.showProviderModal, false)
  } finally {
    unmount()
  }
})

test('默认模型供应商不能停用，新建时切换只改变待提交状态', async () => {
  const { panel, unmount } = mountPanel()
  try {
    configStore.config.default_model = 'sample:model'
    panel.openEditProviderModal(provider)
    panel.toggleProviderEnabled(false)
    assert.deepEqual(requests, [])
    assert.equal(panel.showProviderModal, true)
    assert.equal(panel.originalProviderEnabled, true)
    assert.equal(notices.at(-1)?.[0], 'warning')

    panel.openCreateProviderModal()
    panel.toggleProviderEnabled(false)
    await nextTick()
    assert.equal(panel.providerForm.is_enabled, false)
    assert.deepEqual(requests, [])
  } finally {
    unmount()
  }
})

test('停用请求尚未完成时保持弹窗并锁定表单', async () => {
  const { panel, unmount } = mountPanel()
  try {
    await flushAsync()
    let finish
    updateProvider = () => new Promise((resolve) => (finish = resolve))
    panel.openEditProviderModal(provider)
    panel.toggleProviderEnabled(false)
    assert.equal(panel.saving, true)
    assert.equal(panel.showProviderModal, true)
    assert.equal(requests.length, 1)
    panel.deleteProviderFromEdit()
    assert.deepEqual(confirmations, [])
    finish({})
    await nextTick()
    await nextTick()
    assert.equal(panel.showProviderModal, false)
  } finally {
    unmount()
  }
})
