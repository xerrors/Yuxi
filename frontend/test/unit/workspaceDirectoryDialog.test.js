import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers'
import test from 'node:test'
import { createRenderer, h, nextTick, ref } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createTestServer } from '../helpers/createTestServer.js'

const node = (type) => ({ type, children: [], props: {}, parent: null, text: '', focus() {} })
const renderer = createRenderer({
  createElement: node,
  createText: (text) => ({ ...node('text'), text }),
  createComment: (text) => ({ ...node('comment'), text }),
  insert(child, parent, anchor) {
    if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText: (item, text) => { item.text = text },
  setElementText: (item, text) => { item.text = text; item.children = [] },
  parentNode: (item) => item.parent,
  nextSibling: (item) => item.parent?.children[item.parent.children.indexOf(item) + 1],
  patchProp: (item, key, _old, value) => { item.props[key] = value }
})

const find = (item, predicate) =>
  predicate(item) ? item : item.children.map((child) => find(child, predicate)).find(Boolean)
const textContent = (item) => [item.text, ...item.children.map(textContent)].join('')
const settle = async () => { await new Promise((resolve) => setImmediate(resolve)); await nextTick() }
const searchSettled = async () => { await new Promise((resolve) => setTimeout(resolve, 160)); await settle() }

test('目录选择只接受实际目录，过期路径结果不能覆盖新输入', async () => {
  const tree = {
    '/': ['projects', 'notes'],
    '/projects': ['existing'],
    '/projects/existing': ['child'],
    '/projects/existing/child': []
  }
  let delayedResolve
  let delay = false
  globalThis.__directoryDialogTest = {
    async list(path) {
      if (delay && path === '/projects/existing') {
        delay = false
        await new Promise((resolve) => { delayedResolve = resolve })
      }
      return {
        entries: (tree[path] || []).map((name) => ({
          name,
          is_dir: true,
          path: `${path === '/' ? '' : path}/${name}/`
        }))
      }
    },
    create: async () => ({})
  }
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [{
      name: 'workspace-directory-dialog-test',
      enforce: 'pre',
      resolveId(id) {
        if (id.endsWith('/src/apis/workspace_api.js') || id.endsWith('/src/apis/workspace_api')) return '\0@/apis/workspace_api'
        if (id.endsWith('/src/apis/base.js') || id.endsWith('/src/apis/base')) return '\0@/apis/base'
        if (['virtual:directory-dialog', '@/apis/workspace_api', '@/apis/base'].includes(id)) return `\0${id}`
      },
      load(id) {
        if (id === '\0@/apis/workspace_api') return 'export const getWorkspaceTree = globalThis.__directoryDialogTest.list; export const createWorkspaceDirectory = globalThis.__directoryDialogTest.create'
        if (id === '\0@/apis/base') return 'export const resolveApiErrorMessage = (error, fallback) => error?.message || fallback'
        if (id !== '\0virtual:directory-dialog') return
        const source = readFileSync(new URL('../../src/modules/workspace/ui/WorkspaceDirectoryDialog.vue', import.meta.url), 'utf8')
        return compileScript(parse(source).descriptor, { id: 'directory-dialog', inlineTemplate: true }).content
      }
    }]
  })
  let app
  try {
    const { default: DirectoryDialog } = await server.ssrLoadModule('virtual:directory-dialog')
    const open = ref(false)
    const host = node('root')
    app = renderer.createApp(() => h(DirectoryDialog, { open: open.value, includeUnboundProjectDirs: true }))
    app.component('a-modal', { setup: (_, { attrs, slots }) => () => attrs.open ? h('modal', attrs, slots.default?.()) : null })
    app.component('a-button', { setup: (_, { attrs, slots }) => () => h('button', attrs, slots.default?.()) })
    app.component('a-input', {
      setup(_, { attrs, expose }) {
        expose({ focus() {} })
        return () => h('input', {
          ...attrs,
          onInput: (event) => { attrs['onUpdate:value']?.(event.target.value); attrs.onInput?.(event) }
        })
      }
    })
    app.component('a-spin', { setup: () => () => h('spin') })
    app.mount(host)
    open.value = true
    await settle()
    const byClass = (name) => find(host, (item) => String(item.props.class || '').split(' ').includes(name))
    const row = (name) => find(host, (item) => item.type === 'button' && String(item.props.class || '').includes('directory-row') && textContent(item) === name)
    assert.equal(byClass('directory-open').props.disabled, true)
    row('projects').props.onClick()
    await settle()
    assert.equal(byClass('directory-open').props.disabled, false)
    find(host, (item) => item.props['aria-label'] === '编辑目录路径').props.onClick()
    await settle()
    const input = () => find(host, (item) => item.type === 'input' && item.props['aria-label'] === '编辑目录路径')
    const typePath = (value) => {
      const handlers = [input().props.onInput].flat()
      for (const handler of handlers) handler({ target: { value } })
    }
    typePath('/missing')
    await searchSettled()
    assert.ok(row('projects'))
    assert.ok(row('notes'))
    assert.equal(byClass('directory-open').props.disabled, true)
    delay = true
    typePath('/projects/existing/')
    await searchSettled()
    assert.equal(typeof delayedResolve, 'function')
    typePath('/missing')
    await searchSettled()
    delayedResolve()
    await settle()
    assert.ok(row('notes'))
    assert.equal(row('child'), undefined)
    assert.equal(byClass('directory-open').props.disabled, true)
  } finally {
    app?.unmount()
    await server.close()
    delete globalThis.__directoryDialogTest
  }
})
