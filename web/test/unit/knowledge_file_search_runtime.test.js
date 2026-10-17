import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, reactive } from 'vue'

globalThis.document = { activeElement: null }
globalThis.Document = class {}
globalThis.ShadowRoot = class {}

const compiledPath = fileURLToPath(
  new URL(`../../.knowledge-search-test-${pid}.mjs`, import.meta.url)
)
const dependencyPath = fileURLToPath(
  new URL(`../../.knowledge-search-dependencies-${pid}.mjs`, import.meta.url)
)

const createNode = (type) => ({
  type,
  props: {},
  children: [],
  parent: null,
  text: '',
  value: '',
  tagName: type.toUpperCase(),
  listeners: {},
  getRootNode: () => globalThis.document,
  addEventListener(name, listener) {
    this.listeners[name] = listener
  },
  focus() {}
})
const renderer = createRenderer({
  createElement: createNode,
  createText(text) {
    return { ...createNode('text'), text }
  },
  createComment(text) {
    return { ...createNode('comment'), text }
  },
  insert(child, parent, anchor = null) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText(node, text) {
    node.text = text
  },
  setElementText(node, text) {
    node.text = text
    node.children = []
  },
  parentNode: (node) => node.parent,
  nextSibling(node) {
    const siblings = node.parent?.children || []
    return siblings[siblings.indexOf(node) + 1] || null
  },
  patchProp(node, key, _old, value) {
    node.props[key] = value
  },
  insertStaticContent(text, parent, anchor) {
    const node = { ...createNode('static'), text, parent }
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(node)
    else parent.children.splice(index, 0, node)
    return [node, node]
  }
})

function findInput(node) {
  if (node.type === 'input') return node
  for (const child of node.children) {
    const found = findInput(child)
    if (found) return found
  }
}
const textContent = (node) => node.text + node.children.map(textContent).join('')

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('实际文件搜索弹窗在切库、会话变更、关闭和卸载后清除旧结果并拒绝晚到响应', async (t) => {
  const { descriptor } = parse(
    readFileSync(
      new URL('../../src/components/modals/FileSearchModal.vue', import.meta.url),
      'utf8'
    )
  )
  const dependencies = `./.knowledge-search-dependencies-${pid}.mjs`
  const source = compileScript(descriptor, { id: 'knowledge-search-test', inlineTemplate: true })
    .content // 仅将 DOM 容器与动画换为 Fragment，搜索组件和输入交互保持真实实现。
    .replace('Teleport as _Teleport', 'Fragment as _Teleport')
    .replace('Transition as _Transition, ', '')
    .replace("from '@/utils/time'", "from './src/utils/time.js'")
    .replace("from '@/utils/file_utils'", `from '${dependencies}'`)
    .replace("from '@/apis/knowledge_api'", `from '${dependencies}'`)
    .replace("from '@/stores/user'", `from '${dependencies}'`)
  writeFileSync(
    compiledPath,
    `const _Transition = { inheritAttrs: false, setup(_props, { slots }) { return () => slots.default?.() } };\n${source}`
  )
  writeFileSync(
    dependencyPath,
    `
export const useUserStore = () => globalThis.__knowledgeSearchUser
export const documentApi = { searchDocuments: (...args) => globalThis.__knowledgeSearchRequest(...args) }
export const formatFileSize = value => String(value)
`
  )
  try {
    const { default: component } = await import(
      `${pathToFileURL(compiledPath).href}?test=${Date.now()}`
    )
    for (const change of ['kb-null', 'kb-new', 'session', 'role', 'close', 'unmount']) {
      await t.test(change, async () => {
        const user = reactive({ token: 'old-session', userRole: 'user' })
        const state = reactive({ open: true, kbId: 'old-kb' })
        globalThis.__knowledgeSearchUser = user
        let late
        globalThis.__knowledgeSearchRequest = async () =>
          late?.promise || { files: [{ file_id: 'old', filename: '旧客户资料.md' }] }
        const container = createNode('root')
        const app = renderer.createApp({
          setup: () => () =>
            h(component, {
              open: state.open,
              kbId: state.kbId,
              'onUpdate:open': (value) => {
                state.open = value
              }
            })
        })
        app.mount(container)
        const input = findInput(container)
        input.props['onUpdate:modelValue']('客户')
        await input.props.onKeydown({ key: 'Enter', preventDefault() {} })
        await nextTick()
        assert.match(textContent(container), /旧客户资料.md/)
        late = deferred()
        input.props['onUpdate:modelValue']('第二次搜索')
        const request = input.props.onKeydown({ key: 'Enter', preventDefault() {} })
        if (change === 'kb-null') state.kbId = null
        if (change === 'kb-new') state.kbId = 'new-kb'
        if (change === 'session') user.token = 'new-session'
        if (change === 'role') user.userRole = 'admin'
        if (change === 'close') state.open = false
        if (change === 'unmount') app.unmount()
        await nextTick()
        assert.doesNotMatch(textContent(container), /旧客户资料.md/)
        if (state.open && change !== 'unmount') assert.equal(findInput(container).value, '')
        late.resolve({ files: [{ file_id: 'late', filename: '迟到的私有文件.md' }] })
        await request
        await nextTick()
        assert.doesNotMatch(textContent(container), /迟到的私有文件.md/)
        if (change !== 'unmount') app.unmount()
      })
    }
  } finally {
    unlinkSync(compiledPath)
    unlinkSync(dependencyPath)
    delete globalThis.__knowledgeSearchUser
    delete globalThis.__knowledgeSearchRequest
  }
})
