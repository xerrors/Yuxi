import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'

const renderer = createRenderer({
  createElement: (type) => ({ type, children: [] }),
  createText: (text) => ({ text }),
  createComment: () => ({}),
  insert: (child, parent) => {
    parent.children.push(child)
  },
  remove() {},
  setText() {},
  setElementText() {},
  patchProp() {},
  parentNode: () => null,
  nextSibling: () => null
})

async function loadComponent(relativePath, name) {
  const { descriptor } = parse(readFileSync(new URL(relativePath, import.meta.url), 'utf8'))
  let code = compileScript(descriptor, { id: name })
    .content.replace(/import (\w+) from ['"]@\/components\/[^'"]+['"]/g, 'const $1 = {}')
    .replace(
      /import \{ useThemeStore \} from ['"]@\/stores\/theme['"]/,
      'const useThemeStore = () => ({ isDark: false })'
    )
    .replace(/from ['"]@\/utils\/([^'"]+)['"]/g, "from './src/utils/$1.js'")
    .replace(
      /import \{ skillApi \} from ['"]@\/apis\/skill_api['"]/,
      'const skillApi = globalThis.__skillEditorApi'
    )
    .replace(
      /import \{ useAgentStore \} from ['"]@\/stores\/agent['"]/,
      'const useAgentStore = () => ({ refreshAvailableSkills: async () => {} })'
    )
    .replace(
      /import \{ message \} from ['"]ant-design-vue['"]/,
      'const message = { success() {}, warning() {} }'
    )
  const path = fileURLToPath(new URL(`../../.${name}-runtime.mjs`, import.meta.url))
  writeFileSync(path, code)
  try {
    const component = (await import(pathToFileURL(path).href)).default
    // Lightweight renderer, real compiled SFC setup/watch/props; no browser dependency.
    component.render = () => null
    return component
  } finally {
    unlinkSync(path)
  }
}

test('mounted editor dirty state blocks rename until save/cancel without replacing draft', async () => {
  let writes = 0
  globalThis.__skillEditorApi = {
    updateSkillDisplayName: async () => {
      writes += 1
    }
  }
  const Preview = await loadComponent(
    '../../src/components/AgentFilePreview.vue',
    'skill-editor-preview'
  )
  const Rename = await loadComponent(
    '../../src/components/extensions/SkillDisplayNameButton.vue',
    'skill-editor-rename'
  )
  const preview = ref(null)
  const rename = ref(null)
  const dirty = ref(false)
  const file = ref({ content: 'original body', previewType: 'markdown' })
  const app = renderer.createApp({
    setup: () => () =>
      h('div', [
        h(Preview, {
          ref: preview,
          file: file.value,
          filePath: 'SKILL.md',
          editable: true,
          onDirtyChange: (value) => {
            dirty.value = value
          }
        }),
        h(Rename, {
          ref: rename,
          skill: { slug: 'demo', name: 'Demo', can_manage: true },
          disabled: dirty.value
        })
      ])
  })
  const originalDocument = globalThis.document
  globalThis.document = { body: { style: {} } }
  app.mount({ children: [] })
  try {
    await nextTick()
    const editor = preview.value.$.setupState
    const button = rename.value.$.setupState
    assert.equal(dirty.value, false)
    editor.startEditing()
    editor.draftContent = 'unsaved user body'
    await nextTick()
    assert.equal(dirty.value, true)
    button.startEditing()
    button.name = '新名称'
    await button.save()
    assert.equal(writes, 0)
    assert.equal(editor.draftContent, 'unsaved user body')
    editor.cancelEdit()
    await nextTick()
    assert.equal(dirty.value, false)
    button.startEditing()
    button.name = '新名称'
    await button.save()
    assert.equal(writes, 1)
    editor.startEditing()
    editor.draftContent = 'saved body'
    await nextTick()
    assert.equal(dirty.value, true)
    file.value = { ...file.value, content: 'saved body' }
    await nextTick()
    assert.equal(dirty.value, false)
  } finally {
    app.unmount()
    globalThis.document = originalDocument
    delete globalThis.__skillEditorApi
  }
})
