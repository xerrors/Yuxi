import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { pathToFileURL, fileURLToPath } from 'node:url'
import test from 'node:test'
import { setImmediate } from 'node:timers'
import { parse, compileScript } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'

function createHostNode(type) {
  return { type, props: {}, children: [], parent: null, text: '' }
}

const renderer = createRenderer({
  createElement: createHostNode,
  createText(text) {
    const node = createHostNode('text')
    node.text = text
    return node
  },
  createComment(text) {
    const node = createHostNode('comment')
    node.text = text
    return node
  },
  insert(child, parent, anchor = null) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index >= 0) parent.children.splice(index, 0, child)
    else parent.children.push(child)
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
  parentNode(node) {
    return node.parent
  },
  nextSibling(node) {
    const siblings = node.parent?.children || []
    return siblings[siblings.indexOf(node) + 1] || null
  },
  patchProp(node, key, _previous, value) {
    node.props[key] = value
  }
})

function findNodes(node, predicate, result = []) {
  if (predicate(node)) result.push(node)
  for (const child of node.children || []) findNodes(child, predicate, result)
  return result
}

const flush = async () => {
  await new Promise((resolve) => setImmediate(resolve))
  await nextTick()
}
test('个人回收站真实组件加载、冲突、恢复、错误和到期禁用', async () => {
  const source = readFileSync(
    new URL('../../src/components/workspace/PersonalTrashTable.vue', import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const compiled = compileScript(descriptor, { id: 'personal-trash-test', inlineTemplate: true })
    .content.replace(
      /import \{ personalTrashApi \} from ['"]@\/apis\/personal_trash_api['"]/,
      'const personalTrashApi = globalThis.__personalTrashApi'
    )
    .replace(/import \{ message \} from ['"]ant-design-vue['"]/, 'const message = { success() {} }')
  const path = fileURLToPath(new URL('../../.personal-trash-runtime-test.mjs', import.meta.url))
  writeFileSync(path, compiled)
  let rows = [
    {
      id: 'a',
      name: '文件甲',
      paths: ['/a'],
      kind: 'workspace',
      state: 'trashed',
      purge_after: '2099-01-01'
    },
    {
      id: 'b',
      name: '已到期',
      paths: ['/b'],
      kind: 'attachment',
      state: 'trashed',
      purge_after: '2000-01-01'
    },
    {
      id: 'c',
      name: '待重试',
      paths: ['/c'],
      kind: 'attachment',
      state: 'pending_delete',
      purge_after: '2099-01-01'
    }
  ]
  let failed = false
  let conflict = true
  const restored = []
  globalThis.__personalTrashApi = {
    async list() {
      if (failed) throw new Error('连接失败')
      return { items: [...rows], total: rows.length }
    },
    async restore(id) {
      if (conflict) throw new Error('原位置冲突')
      restored.push(id)
      rows = rows.filter((row) => row.id !== id)
    },
    async retry(id) {
      rows = rows.map((row) => (row.id === id ? { ...row, state: 'trashed' } : row))
    }
  }
  const root = createHostNode('root')
  let app
  try {
    const component = (await import(`${pathToFileURL(path).href}?t=${Date.now()}`)).default
    app = renderer.createApp(component)
    app.component('a-table', {
      props: ['columns', 'dataSource'],
      setup(props, { slots }) {
        return () =>
          h(
            'table',
            props.dataSource.map((record) =>
              h(
                'row',
                { id: record.id },
                props.columns.map((column) => slots.bodyCell({ column, record }))
              )
            )
          )
      }
    })
    app.component('a-button', {
      inheritAttrs: false,
      setup(_, { attrs, slots }) {
        return () => h('button', attrs, slots.default?.())
      }
    })
    app.component('a-space', {
      setup(_, { slots }) {
        return () => h('div', slots.default?.())
      }
    })
    app.component('a-alert', {
      props: ['message'],
      setup(props) {
        return () => h('alert', props.message)
      }
    })
    app.mount(root)
    await flush()
    const getRow = (id) => findNodes(root, (node) => node.type === 'row' && node.props.id === id)[0]
    const rowButton = (id) => findNodes(getRow(id), (node) => node.type === 'button')[0]
    assert.ok(getRow('a'))
    assert.equal(rowButton('b').props.disabled, true)
    assert.ok(findNodes(getRow('b'), (node) => node.text.includes('已到期，等待清理')).length > 0)
    await rowButton('a').props.onClick()
    await flush()
    assert.equal(restored.length, 0)
    assert.ok(
      findNodes(root, (node) => node.type === 'alert').some((node) =>
        node.text.includes('原位置冲突')
      )
    )
    conflict = false
    await rowButton('a').props.onClick()
    await flush()
    assert.deepEqual(restored, ['a'])
    assert.equal(getRow('a'), undefined)
    await rowButton('c').props.onClick()
    await flush()
    assert.equal(rows.find((row) => row.id === 'c').state, 'trashed')
    failed = true
    await findNodes(root, (node) => node.type === 'button')[0].props.onClick()
    await flush()
    assert.ok(
      findNodes(root, (node) => node.type === 'alert').some((node) =>
        node.text.includes('连接失败')
      )
    )
  } finally {
    app?.unmount()
    unlinkSync(path)
    delete globalThis.__personalTrashApi
  }
})
