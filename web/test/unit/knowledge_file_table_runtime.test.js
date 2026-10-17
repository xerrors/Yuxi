import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, nextTick, reactive } from 'vue'

const compiledPath = fileURLToPath(new URL(`../../.knowledge-table-${pid}.mjs`, import.meta.url))
const dependencyPath = fileURLToPath(
  new URL(`../../.knowledge-table-deps-${pid}.mjs`, import.meta.url)
)
const renderer = createRenderer({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert(node, parent) {
    node.parent = parent
  },
  remove() {},
  setText() {},
  setElementText() {},
  parentNode: (node) => node.parent,
  nextSibling: () => null,
  patchProp() {}
})

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('实际文件表格在失去编辑权、切库或会话变化后关闭操作并拒绝迟到参数', async (t) => {
  const { descriptor } = parse(
    readFileSync(new URL('../../src/components/FileTable.vue', import.meta.url), 'utf8')
  )
  const dependency = `./.knowledge-table-deps-${pid}.mjs`
  const source = compileScript(descriptor, { id: 'knowledge-table-test' })
    .content.replace(
      /from '(?:ant-design-vue|@\/stores\/(?:user|database|config)|@\/apis\/knowledge_api)'/g,
      `from '${dependency}'`
    )
    .replace("from '@/utils/file_utils'", `from '${dependency}'`)
    .replace(/from '@\/utils\/([^']+)'/g, "from './src/utils/$1.js'")
    .replace(/import (\w+) from '@\/components\/[^']+'/g, 'const $1 = {}')
  writeFileSync(compiledPath, source)
  writeFileSync(
    dependencyPath,
    `
export const useUserStore = () => globalThis.__knowledgeTableUser
export const useDatabaseStore = () => globalThis.__knowledgeTableStore
export const useConfigStore = () => ({ config: { default_ocr_engine: 'disable' } })
export const parseDownloadFilename = () => null
export const documentApi = {
  getDocumentInfo: (...args) => globalThis.__knowledgeTableRead(...args),
  createFolder: async (...args) => globalThis.__knowledgeTableWrites.push(['folder', ...args]),
  renameFolder: async (...args) => globalThis.__knowledgeTableWrites.push(['rename', ...args])
}
export const message = { success() {}, warning() {}, error() {}, info() {} }
export const Modal = { confirm: options => {
  const dialog = { options, destroyed: false, destroy() { this.destroyed = true } }
  globalThis.__knowledgeTableDialogs.push(dialog)
  return dialog
} }
`
  )
  try {
    const { default: component } = await import(pathToFileURL(compiledPath).href)
    for (const change of ['readonly', 'kb', 'token', 'role', 'unmount', 'current']) {
      await t.test(change, async () => {
        const user = reactive({ token: 'old', userRole: 'user', isAdmin: false })
        const props = reactive({ readonly: false })
        const writes = []
        const dialogs = []
        const store = reactive({
          kbId: 'old-kb',
          documentFiles: [],
          selectedRowKeys: [],
          folderBreadcrumbs: [],
          state: { chunkLoading: false, lock: false },
          fileBrowser: { parentId: null },
          resetFileBrowser() {},
          loadDocumentFiles: async () => {},
          getDatabaseInfo: async () => {},
          parseFiles: async (...args) => {
            writes.push(['parse', ...args])
            return true
          },
          indexFiles: async (...args) => {
            writes.push(['index', ...args])
            return true
          },
          deleteFile: async (...args) => {
            writes.push(['delete', ...args])
            return true
          }
        })
        globalThis.__knowledgeTableUser = user
        globalThis.__knowledgeTableStore = store
        globalThis.__knowledgeTableWrites = writes
        globalThis.__knowledgeTableDialogs = dialogs
        globalThis.__knowledgeTableRead = async () => ({
          processing_params: { ocr_engine: 'old-private-ocr' }
        })
        let bindings
        const app = renderer.createApp({
          setup() {
            bindings = component.setup(props, { emit() {}, expose() {} })
            return () => null
          }
        })
        app.mount({})
        await bindings.handleParseFile({ file_id: 'old-file' })
        assert.equal(bindings.parseConfigModalVisible.value, true)
        await bindings.handleIndexFile({
          file_id: 'old-file',
          processing_params: { chunk_preset_id: 'private-preset' }
        })
        bindings.showCreateFolderModal('private-parent')
        bindings.newFolderName.value = '旧目录'
        bindings.showRenameFolderModal({ file_id: 'old-folder', filename: '旧目录' })
        bindings.handleDeleteFolder({ file_id: 'old-folder', filename: '旧目录' })
        if (change === 'current') {
          await bindings.handleParseConfigConfirm()
          await bindings.handleIndexConfigConfirm()
          await bindings.handleCreateFolder()
          await bindings.handleRenameFolder()
          assert.deepEqual(
            writes.map((item) => item[0]),
            ['parse', 'index', 'folder', 'rename']
          )
          app.unmount()
          return
        }
        const late = deferred()
        globalThis.__knowledgeTableRead = () => late.promise
        const oldRequest = bindings.handleParseFile({ file_id: 'late-old-file' })
        if (change === 'readonly') props.readonly = true
        if (change === 'kb') store.kbId = 'new-kb'
        if (change === 'token') user.token = 'new'
        if (change === 'role') user.userRole = 'admin'
        if (change === 'unmount') app.unmount()
        assert.equal(bindings.parseConfigModalVisible.value, false)
        assert.equal(bindings.indexConfigModalVisible.value, false)
        assert.equal(bindings.createFolderModalVisible.value, false)
        assert.equal(bindings.renameFolderModalVisible.value, false)
        assert.equal(bindings.newFolderName.value, '')
        assert.equal(bindings.folderBeingRenamed.value, null)
        assert.deepEqual(bindings.currentParseFileIds.value, [])
        assert.equal(dialogs[0].destroyed, true)
        await dialogs[0].options.onOk()
        await bindings.handleParseConfigConfirm()
        await bindings.handleIndexConfigConfirm()
        await bindings.handleCreateFolder()
        late.resolve({ processing_params: { ocr_engine: 'late-private-ocr' } })
        await oldRequest
        await nextTick()
        assert.equal(bindings.parseConfigModalVisible.value, false)
        assert.equal(bindings.parseParams.value.ocr_engine, 'disable')
        assert.deepEqual(writes, [])
        if (change !== 'unmount') app.unmount()
      })
    }
  } finally {
    unlinkSync(compiledPath)
    unlinkSync(dependencyPath)
    for (const key of ['User', 'Store', 'Writes', 'Dialogs', 'Read'])
      delete globalThis[`__knowledgeTable${key}`]
  }
})
