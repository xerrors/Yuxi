import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { setImmediate } from 'node:timers'
import test from 'node:test'
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
async function mountPanel(name, api) {
  const source = readFileSync(new URL(`../../src/components/${name}.vue`, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  let compiled = compileScript(descriptor, { id: name, inlineTemplate: true })
    .content.replace(
      /import \{ documentLimitsApi \} from ['"]@\/apis\/system_api['"]/,
      'const documentLimitsApi = globalThis.__settingsApi'
    )
  const path = fileURLToPath(new URL(`../../.${name}-runtime.mjs`, import.meta.url))
  writeFileSync(path, compiled)
  globalThis.__settingsApi = api
  const { default: Component } = await import(pathToFileURL(path).href + `?${Date.now()}`)
  const root = createHostNode('root')
  const app = renderer.createApp(Component)
  for (const name of [
    'a-spin',
    'a-alert',
    'a-form',
    'a-form-item',
    'a-input',
    'a-input-number',
    'a-space',
    'a-button',
    'a-popconfirm'
  ]) {
    app.component(name, {
      setup:
        (_, { slots, attrs }) =>
        () =>
          h(name, attrs, slots.default?.())
    })
  }
  app.mount(root)
  await flush()
  return {
    root,
    nodes: (type) => findNodes(root, (n) => n.type === type),
    close: () => {
      app.unmount()
      unlinkSync(path)
      delete globalThis.__settingsApi
    }
  }
}

test('文档限制真实组件：部署上限、非法输入、版本冲突和恢复默认', async () => {
  const current = {
    upload_max_mib: 32,
    ocr_max_pages: 100,
    hard_upload_max_mib: 64,
    hard_ocr_max_pages: 500,
    effective_upload_max_bytes: 33554432,
    defaults: { upload_max_mib: 16, ocr_max_pages: 50 },
    revision: 4
  }
  let writes = 0,
    conflict = true
  const panel = await mountPanel('DocumentLimitsSettings', {
    get: async () => current,
    update: async (value) => {
      writes++
      assert.deepEqual(value, { revision: 4, upload_max_mib: 48, ocr_max_pages: 200 })
      if (conflict) throw Object.assign(new Error('stale'), { status: 409 })
      return { ...current, ...value, revision: 5 }
    },
    reset: async (revision) => {
      assert.equal(revision, 5)
      return { ...current, ...current.defaults, revision: 6 }
    }
  })
  try {
    assert.equal(panel.nodes('a-input-number')[0].props.max, 64)
    for (const value of [0, -1, 1.5, 65, null]) {
      panel.nodes('a-input-number')[0].props['onUpdate:value'](value)
      await flush()
      assert.equal(panel.nodes('a-button')[0].props.disabled, true)
      await panel
        .nodes('a-button')
        .find((node) => node.props.type === 'primary')
        .props.onClick()
    }
    assert.equal(writes, 0)
    panel.nodes('a-input-number')[0].props['onUpdate:value'](48)
    panel.nodes('a-input-number')[1].props['onUpdate:value'](200)
    await flush()
    await panel
      .nodes('a-button')
      .find((node) => node.props.type === 'primary')
      .props.onClick()
    await flush()
    assert.equal(panel.nodes('a-input-number')[0].props.value, 48)
    assert.ok(panel.nodes('a-alert').some((n) => n.props.message.includes('重新加载')))
    conflict = false
    await panel
      .nodes('a-button')
      .find((node) => node.props.type === 'primary')
      .props.onClick()
    await flush()
    await panel.nodes('a-popconfirm')[0].props.onConfirm()
    await flush()
    assert.equal(panel.nodes('a-input-number')[0].props.value, 16)
    assert.equal(panel.nodes('a-input-number')[1].props.value, 50)
  } finally {
    panel.close()
  }
})
