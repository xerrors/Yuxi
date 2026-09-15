import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { pathToFileURL, fileURLToPath } from 'node:url'
import test from 'node:test'
import { setImmediate } from 'node:timers'
import { parse, compileScript } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'

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
test('回收站真实组件加载、恢复、失败、到期禁用与跨库响应隔离', async () => {
  const source = readFileSync(
    new URL('../../src/components/knowledge/TrashDrawer.vue', import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const compiled = compileScript(descriptor, { id: 'trash-test', inlineTemplate: true })
    .content.replace(
      /import \{ documentApi \} from ['"]@\/apis\/knowledge_api['"]/,
      'const documentApi = globalThis.__trashApi'
    )
    .replace(/import \{ message \} from ['"]ant-design-vue['"]/, 'const message = { success() {} }')
  const path = fileURLToPath(new URL('../../.trash-runtime-test.mjs', import.meta.url))
  writeFileSync(path, compiled)
  let rows = [
    {
      file_id: 'a',
      filename: '合同.pdf',
      status: 'trashed',
      deleted_at: '2026-01-01',
      purge_after: '2099-01-01'
    },
    { file_id: 'expired', filename: '过期.pdf', status: 'trashed', purge_after: '2000-01-01' },
    { file_id: 'busy', filename: '清理.pdf', status: 'purge_failed', purge_after: '2099-01-01' }
  ]
  let fail = false
  let restoreFail = true
  let requestedPage
  let restored = 0
  let delayed
  globalThis.__trashApi = {
    getTrash: async (kb, page) => {
      requestedPage = page
      if (kb === 'slow')
        return new Promise((resolve) => {
          delayed = resolve
        })
      if (fail) throw new Error('读取失败')
      return { items: rows, total: rows.length }
    },
    restoreDocument: async (kb, id) => {
      assert.equal(kb, 'kb')
      assert.equal(id, 'a')
      if (restoreFail) throw new Error('恢复冲突')
      rows = rows.filter((r) => r.file_id !== id)
      return { restored_count: 1 }
    }
  }
  let app
  try {
    const { default: Drawer } = await import(pathToFileURL(path).href)
    const open = ref(true),
      kbId = ref('kb')
    const root = createHostNode('root')
    app = renderer.createApp({
      setup: () => () =>
        h(Drawer, { open: open.value, kbId: kbId.value, onRestored: () => restored++ })
    })
    for (const name of ['a-drawer', 'a-space', 'a-button', 'a-alert']) {
      app.component(name, {
        setup:
          (_, { slots, attrs }) =>
          () =>
            h(name, attrs, slots.default?.())
      })
    }
    app.component('a-table', {
      props: ['dataSource', 'columns', 'loading', 'pagination'],
      setup:
        (props, { slots, attrs }) =>
        () =>
          h(
            'table',
            { loading: props.loading, onChange: attrs.onChange },
            props.dataSource.flatMap((record) =>
              props.columns.map((column) => h('cell', {}, slots.bodyCell({ column, record })))
            )
          )
    })
    app.mount(root)
    await flush()
    const buttons = () => findNodes(root, (n) => n.type === 'a-button')
    assert.equal(buttons().length, 4)
    assert.equal(buttons()[1].props.disabled, false)
    assert.equal(buttons()[2].props.disabled, true)
    assert.ok(findNodes(root, (node) => node.text.includes('已到期，等待清理')).length > 0)
    assert.equal(buttons()[3].props.disabled, true)
    await buttons()[3].props.onClick()
    await flush()
    assert.equal(restored, 0)
    findNodes(root, (n) => n.type === 'table')[0].props.onChange({ current: 2 })
    await flush()
    assert.equal(requestedPage, 2)
    await buttons()[1].props.onClick()
    await flush()
    assert.equal(restored, 0)
    assert.equal(buttons().length, 4)
    assert.ok(findNodes(root, (n) => n.props?.message === '恢复冲突').length)
    restoreFail = false
    await buttons()[1].props.onClick()
    await flush()
    assert.equal(restored, 1)
    assert.equal(buttons().length, 3)
    fail = true
    await buttons()[0].props.onClick()
    await flush()
    assert.ok(findNodes(root, (n) => n.props?.message === '读取失败').length)
    fail = false
    kbId.value = 'slow'
    await flush()
    kbId.value = 'new'
    rows = []
    await flush()
    delayed({ items: [{ file_id: 'stale', filename: '旧库秘密' }], total: 1 })
    await flush()
    assert.equal(buttons().length, 1)
    assert.equal(findNodes(root, (n) => n.text?.includes('旧库秘密')).length, 0)
  } finally {
    app?.unmount()
    unlinkSync(path)
    delete globalThis.__trashApi
  }
})

test('统一入口实际组件按原库组合并保留列表失败状态', async () => {
  const source = readFileSync(
    new URL('../../src/views/GlobalTrashView.vue', import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const compiled = compileScript(descriptor, { id: 'global-trash-test', inlineTemplate: true })
    .content.replace(
      /import \{ databaseApi \} from ['"]@\/apis\/knowledge_api['"]/,
      'const databaseApi = globalThis.__globalTrashApi'
    )
    .replace(
      /import PageHeader from [^\n]+/,
      "import PageHeader from './.global-trash-header-test.mjs'"
    )
    .replace(/import TrashDrawer from [^\n]+/, "const TrashDrawer = 'trash-content'")
    .replace(
      /import PersonalTrashTable from [^\n]+/,
      "const PersonalTrashTable = 'personal-trash-content'"
    )
  const headerPath = fileURLToPath(new URL('../../.global-trash-header-test.mjs', import.meta.url))
  const headerSource = readFileSync(
    new URL('../../src/components/shared/PageHeader.vue', import.meta.url),
    'utf8'
  )
  writeFileSync(
    headerPath,
    compileScript(parse(headerSource).descriptor, { id: 'trash-header', inlineTemplate: true })
      .content
  )
  const path = fileURLToPath(new URL('../../.global-trash-runtime-test.mjs', import.meta.url))
  writeFileSync(path, compiled)
  let fail = false
  globalThis.__globalTrashApi = {
    getTrashDatabases: async () => {
      if (fail) throw new Error('列表读取失败')
      return {
        databases: [
          { kb_id: 'a', name: '甲库' },
          { kb_id: 'b', name: '乙库' }
        ]
      }
    }
  }
  let app
  try {
    const { default: View } = await import(pathToFileURL(path).href)
    const root = createHostNode('root')
    app = renderer.createApp(View)
    for (const name of ['a-button', 'a-alert', 'a-spin', 'a-empty']) {
      app.component(name, {
        setup:
          (_, { slots, attrs }) =>
          () =>
            h(name, attrs, slots.default?.())
      })
    }
    app.mount(root)
    await flush()
    assert.deepEqual(
      findNodes(root, (n) => n.type === 'trash-content').map((n) => n.props['kb-id']),
      ['a', 'b']
    )
    assert.equal(findNodes(root, (n) => n.type === 'h2' && n.text.includes('原知识库')).length, 2)
    assert.equal(findNodes(root, (n) => n.type === 'personal-trash-content').length, 1)
    fail = true
    await findNodes(root, (n) => n.type === 'a-button')[0].props.onClick()
    await flush()
    assert.equal(findNodes(root, (n) => n.type === 'trash-content').length, 0)
    assert.equal(findNodes(root, (n) => n.type === 'a-empty').length, 0)
    assert.equal(findNodes(root, (n) => n.props.message === '列表读取失败').length, 1)
  } finally {
    app?.unmount()
    unlinkSync(path)
    unlinkSync(headerPath)
    delete globalThis.__globalTrashApi
  }
})
