import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, nextTick, reactive } from 'vue'

const compiledPath = fileURLToPath(
  new URL(`../../.knowledge-upload-test-${pid}.mjs`, import.meta.url)
)
const dependencyPath = fileURLToPath(
  new URL(`../../.knowledge-upload-dependencies-${pid}.mjs`, import.meta.url)
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

test('实际文件夹上传普通用户使用叶子名且保留目录映射，URL模式仅管理员可选', async (t) => {
  const { descriptor } = parse(
    readFileSync(new URL('../../src/components/FileUploadModal.vue', import.meta.url), 'utf8')
  )
  const dependencies = `./.knowledge-upload-dependencies-${pid}.mjs`
  const source = compileScript(descriptor, { id: 'knowledge-upload-test' })
    .content.replace("from 'ant-design-vue'", `from '${dependencies}'`)
    .replace("from '@/stores/user'", `from '${dependencies}'`)
    .replace("from '@/stores/config'", `from '${dependencies}'`)
    .replace("from '@/stores/database'", `from '${dependencies}'`)
    .replace("from '@/apis/knowledge_api'", `from '${dependencies}'`)
    .replace("from '@/utils/chunkUtils'", "from './src/utils/chunkUtils.js'")
    .replace(/import (\w+) from '@\/components\/[^']+'/g, 'const $1 = {}')
  writeFileSync(compiledPath, source)
  writeFileSync(
    dependencyPath,
    `
export const message = { error() {}, warning() {}, info() {}, success() {}, loading: () => () => {} }
export const Upload = { LIST_IGNORE: 'ignore' }
export const Modal = {
  confirm(options) {
    const dialog = { options, destroyed: 0, destroy() { this.destroyed += 1 } }
    globalThis.__knowledgeUploadDialogs?.push(dialog)
    return dialog
  }
}
export const useUserStore = () => globalThis.__knowledgeUploadUser
export const useConfigStore = () => ({ config: { default_ocr_engine: 'disable' } })
export const useDatabaseStore = () => globalThis.__knowledgeUploadStore
export const fileApi = {
  getUploadUrl: id => '/api/knowledge/files/upload?kb_id=' + id,
  getSupportedFileTypes: async () => ({ file_types: ['.md'] }),
  importWorkspaceFiles: (...args) => globalThis.__knowledgeUploadApi.importWorkspaceFiles(...args),
  fetchUrl: (...args) => globalThis.__knowledgeUploadApi.fetchUrl(...args)
}
export const documentApi = {
  deleteDocument: (...args) => globalThis.__knowledgeUploadApi.deleteDocument(...args)
}
`
  )
  const oldXhr = globalThis.XMLHttpRequest
  try {
    const { default: component } = await import(
      `${pathToFileURL(compiledPath).href}?test=${Date.now()}`
    )
    const flush = async () => {
      for (let index = 0; index < 5; index += 1) await Promise.resolve()
      await nextTick()
    }
    const mountUpload = async (role = 'user') => {
      const user = reactive({
        token: 'upload-session',
        userRole: role,
        get isAdmin() {
          return this.userRole === 'admin'
        },
        getAuthHeaders() {
          return { Authorization: `Bearer ${this.token}` }
        }
      })
      const ingests = []
      const events = []
      const store = reactive({
        kbId: 'shared',
        database: {},
        state: { chunkLoading: false },
        addFiles: async (data) => ingests.push(data)
      })
      globalThis.__knowledgeUploadUser = user
      globalThis.__knowledgeUploadStore = store
      const props = reactive({
        visible: false,
        mode: 'file',
        isFolderMode: false,
        currentFolderId: null,
        folderTree: []
      })
      let bindings
      const app = renderer.createApp({
        setup() {
          bindings = component.setup(props, {
            emit: (...args) => events.push(args),
            expose() {}
          })
          return () => null
        }
      })
      app.mount({})
      props.visible = true
      await nextTick()
      bindings.processingParams.value.ocr_engine = 'disable'
      return { user, store, props, bindings, app, ingests, events }
    }
    for (const role of ['user', 'admin']) {
      await t.test(role, async () => {
        const user = reactive({
          userRole: role,
          get isAdmin() {
            return this.userRole === 'admin'
          },
          getAuthHeaders: () => ({ Authorization: 'Bearer upload-session' })
        })
        const ingests = []
        const store = reactive({
          kbId: 'shared',
          database: {},
          state: { chunkLoading: false },
          addFiles: async (data) => ingests.push(data)
        })
        globalThis.__knowledgeUploadUser = user
        globalThis.__knowledgeUploadStore = store
        const requests = []
        globalThis.XMLHttpRequest = class {
          upload = {}
          headers = {}
          status = 200
          responseText = JSON.stringify({
            file_path: 'minio://shared/readme.md',
            content_hash: 'fixture-hash'
          })
          open(method, url) {
            this.method = method
            this.url = url
          }
          setRequestHeader(key, value) {
            this.headers[key] = value
          }
          send(body) {
            requests.push({ body, headers: this.headers, method: this.method, url: this.url })
            this.onload()
          }
        }
        const props = reactive({
          visible: false,
          mode: 'folder',
          isFolderMode: true,
          currentFolderId: null,
          folderTree: []
        })
        let bindings
        const app = renderer.createApp({
          setup() {
            bindings = component.setup(props, { emit() {}, expose() {} })
            return () => null
          }
        })
        app.mount({})
        try {
          props.visible = true
          await nextTick()
          const file = new File(['# 文档'], 'readme.md', { type: 'text/markdown' })
          Object.defineProperty(file, 'webkitRelativePath', { value: '资料/子目录/readme.md' })
          await bindings.runUploadTask({
            options: {
              file,
              onProgress() {},
              onSuccess() {},
              onError(error) {
                throw error
              }
            },
            canceled: false
          })
          assert.equal(
            requests[0].body.get('file').name,
            role === 'user' ? 'readme.md' : '资料/子目录/readme.md'
          )
          assert.equal(requests[0].headers.Authorization, 'Bearer upload-session')
          assert.equal(requests[0].url, '/api/knowledge/files/upload?kb_id=shared')
          bindings.fileList.value = [
            {
              name: 'readme.md',
              webkitRelativePath: file.webkitRelativePath,
              status: 'done',
              response: { file_path: 'minio://shared/readme.md' }
            }
          ]
          await nextTick()
          bindings.processingParams.value.ocr_engine = 'disable'
          await bindings.chunkData()
          assert.equal(
            ingests[0].params.source_paths['minio://shared/readme.md'],
            '资料/子目录/readme.md'
          )
          assert.equal(
            bindings.uploadModeOptions.value.some((option) => option.value === 'url'),
            role === 'admin'
          )
          bindings.uploadMode.value = 'url'
          await nextTick()
          assert.equal(bindings.uploadMode.value, role === 'admin' ? 'url' : 'file')
          if (role === 'admin') {
            user.userRole = 'user'
            assert.equal(bindings.uploadMode.value, 'file')
            assert.equal(
              bindings.uploadModeOptions.value.some((option) => option.value === 'url'),
              false
            )
          }
        } finally {
          app.unmount()
        }
      })
    }

    for (const change of ['kb', 'token', 'role', 'close', 'unmount']) {
      await t.test(`上传队列在 ${change} 后终止且迟到回调无副作用`, async () => {
        const requests = []
        globalThis.XMLHttpRequest = class {
          upload = {}
          headers = {}
          status = 200
          responseText = JSON.stringify({ file_path: 'minio://shared/old.md' })
          open(method, url) {
            this.url = url
          }
          setRequestHeader(key, value) {
            this.headers[key] = value
          }
          send() {
            requests.push(this)
          }
          abort() {
            this.aborted = true
            this.onabort()
          }
        }
        const fixture = await mountUpload()
        const { user, store, props, bindings, app } = fixture
        const callbacks = []
        try {
          const files = Array.from({ length: 12 }, (_, index) => {
            const file = new File(['# 旧文档'], `old-${index}.md`)
            file.uid = `old-${index}`
            return file
          })
          bindings.fileList.value = files
          await nextTick()
          for (const file of files) {
            await bindings.customRequest({
              file,
              onProgress: () => callbacks.push('progress'),
              onSuccess: () => callbacks.push('success'),
              onError: () => callbacks.push('error')
            })
          }
          assert.equal(requests.length, 10)
          assert.equal(bindings.uploadQueue.value.length, 2)
          if (change === 'kb') store.kbId = 'other'
          if (change === 'token') user.token = 'new-session'
          if (change === 'role') user.userRole = 'admin'
          if (change === 'close') props.visible = false
          if (change === 'unmount') app.unmount()
          assert.equal(bindings.uploadQueue.value.length, 0)
          assert.equal(bindings.fileList.value.length, 0)
          assert.ok(requests.every((request) => request.aborted))
          for (const request of requests) {
            request.upload.onprogress({ lengthComputable: true, loaded: 1, total: 2 })
            request.onload()
            request.onerror(new Error('late error'))
          }
          await flush()
          assert.deepEqual(callbacks, [])
          assert.equal(requests.length, 10)
          assert.equal(bindings.activeUploadCount.value, 0)
          assert.deepEqual(bindings.uploadTaskStatus.value, {})
          assert.deepEqual(bindings.uploadTaskProgress.value, {})
          assert.ok(requests.every((request) => request.url.endsWith('kb_id=shared')))
          assert.ok(
            requests.every((request) => request.headers.Authorization === 'Bearer upload-session')
          )
        } finally {
          if (change !== 'unmount') app.unmount()
        }
      })
    }

    await t.test('当前会话完成上传后队列继续处理全部文件', async () => {
      const requests = []
      globalThis.XMLHttpRequest = class {
        upload = {}
        status = 200
        responseText = JSON.stringify({ file_path: 'minio://shared/current.md' })
        open() {}
        setRequestHeader() {}
        send() {
          requests.push(this)
        }
        abort() {
          this.onabort()
        }
      }
      const { bindings, app } = await mountUpload()
      let successes = 0
      try {
        for (let index = 0; index < 12; index += 1) {
          await bindings.customRequest({
            file: new File(['# 文档'], `current-${index}.md`),
            onProgress() {},
            onSuccess: () => successes++,
            onError: (error) => {
              throw error
            }
          })
        }
        assert.equal(requests.length, 10)
        for (let index = 0; index < requests.length; index += 1) {
          requests[index].onload()
          await flush()
        }
        assert.equal(requests.length, 12)
        assert.equal(successes, 12)
        assert.equal(bindings.activeUploadCount.value, 0)
      } finally {
        app.unmount()
      }
    })

    for (const change of ['kb', 'token', 'role', 'close', 'unmount']) {
      await t.test(`个人空间导入在 ${change} 后不入库或覆盖新 loading`, async () => {
        let resolveImport
        const imports = []
        globalThis.__knowledgeUploadApi = {
          importWorkspaceFiles: (id, paths) => {
            imports.push({ id, paths })
            return new Promise((resolve) => (resolveImport = resolve))
          }
        }
        const fixture = await mountUpload()
        const { bindings, app, store, user, props, ingests, events } = fixture
        try {
          bindings.uploadMode.value = 'workspace'
          await nextTick()
          bindings.selectedWorkspacePaths.value = ['projects/old/readme.md']
          const pending = bindings.chunkData()
          assert.deepEqual(imports, [{ id: 'shared', paths: ['projects/old/readme.md'] }])
          if (change === 'kb') store.kbId = 'other'
          if (change === 'token') user.token = 'new-session'
          if (change === 'role') user.userRole = 'admin'
          if (change === 'close') props.visible = false
          if (change === 'unmount') app.unmount()
          assert.deepEqual(bindings.selectedWorkspacePaths.value, [])
          if (change === 'close' || change === 'unmount') {
            assert.equal(store.state.chunkLoading, false)
          }
          store.state.chunkLoading = true
          resolveImport({ items: [{ file_path: 'minio://shared/old.md' }] })
          await pending
          assert.deepEqual(ingests, [])
          assert.deepEqual(events, [])
          assert.equal(store.state.chunkLoading, true)
        } finally {
          if (change !== 'unmount') app.unmount()
        }
      })
    }

    await t.test('当前会话个人空间导入正常入库并结束 loading', async () => {
      globalThis.__knowledgeUploadApi = {
        importWorkspaceFiles: async () => ({ items: [{ file_path: 'minio://shared/current.md' }] })
      }
      const { bindings, app, store, ingests, events } = await mountUpload()
      try {
        bindings.uploadMode.value = 'workspace'
        await nextTick()
        bindings.selectedWorkspacePaths.value = ['projects/current/readme.md']
        await bindings.chunkData()
        assert.equal(ingests.length, 1)
        assert.deepEqual(ingests[0].items, ['minio://shared/current.md'])
        assert.deepEqual(events, [['success'], ['update:visible', false]])
        assert.equal(store.state.chunkLoading, false)
      } finally {
        app.unmount()
      }
    })

    await t.test('URL管理员切会话后迟到内容不回填', async () => {
      let resolveFetch
      const fetches = []
      globalThis.__knowledgeUploadApi = {
        fetchUrl: (url, id) => {
          fetches.push({ url, id })
          return new Promise((resolve) => (resolveFetch = resolve))
        }
      }
      const { bindings, app, user } = await mountUpload('admin')
      try {
        bindings.uploadMode.value = 'url'
        await nextTick()
        bindings.newUrl.value = 'https://example.com/old'
        const pending = bindings.handleFetchUrls()
        assert.deepEqual(fetches, [{ url: 'https://example.com/old', id: 'shared' }])
        const oldItem = bindings.urlList.value[0]
        user.token = 'new-session'
        resolveFetch({
          file_path: 'minio://shared/old.md',
          same_name_files: [{ file_id: 'private-old-name' }]
        })
        await pending
        assert.equal(oldItem.status, 'fetching')
        assert.deepEqual(bindings.urlList.value, [])
        assert.deepEqual(bindings.sameNameFiles.value, [])
        assert.equal(bindings.fetchingUrls.value, false)
      } finally {
        app.unmount()
      }
    })

    await t.test('当前管理员 URL 内容正常回填并入库', async () => {
      globalThis.__knowledgeUploadApi = {
        fetchUrl: async () => ({ file_path: 'minio://shared/current.md', filename: 'current.md' })
      }
      const { bindings, app, store, ingests, events } = await mountUpload('admin')
      try {
        bindings.uploadMode.value = 'url'
        await nextTick()
        bindings.newUrl.value = 'https://example.com/current'
        await bindings.handleFetchUrls()
        assert.equal(bindings.urlList.value[0].status, 'success')
        assert.equal(bindings.fetchingUrls.value, false)
        await bindings.chunkData()
        assert.deepEqual(ingests[0].items, ['minio://shared/current.md'])
        assert.deepEqual(events, [['success'], ['update:visible', false]])
        assert.equal(store.state.chunkLoading, false)
      } finally {
        app.unmount()
      }
    })

    for (const change of ['kb', 'token', 'role', 'close', 'unmount']) {
      await t.test(`同名删除确认在 ${change} 后销毁且旧 onOk 不删除`, async () => {
        const deletes = []
        globalThis.__knowledgeUploadApi = {
          deleteDocument: async (...args) => deletes.push(args)
        }
        globalThis.__knowledgeUploadDialogs = []
        const { bindings, app, store, user, props } = await mountUpload()
        try {
          const file = { file_id: 'old-private-file', filename: '旧同名.md' }
          bindings.sameNameFiles.value = [file]
          bindings.deleteSameNameFile(file)
          const dialog = globalThis.__knowledgeUploadDialogs[0]
          assert.ok(dialog)
          if (change === 'kb') store.kbId = 'other'
          if (change === 'token') user.token = 'new-session'
          if (change === 'role') user.userRole = 'admin'
          if (change === 'close') props.visible = false
          if (change === 'unmount') app.unmount()
          assert.equal(dialog.destroyed, 1)
          await dialog.options.onOk()
          assert.deepEqual(deletes, [])
          assert.deepEqual(bindings.sameNameFiles.value, [])
        } finally {
          if (change !== 'unmount') app.unmount()
        }
      })
    }

    await t.test('当前同名删除确认只删除原库文件并更新同名数量', async () => {
      const deletes = []
      globalThis.__knowledgeUploadApi = {
        deleteDocument: async (...args) => deletes.push(args)
      }
      globalThis.__knowledgeUploadDialogs = []
      const { bindings, app } = await mountUpload()
      try {
        const file = { file_id: 'current-file', filename: '同名.md' }
        bindings.sameNameFiles.value = [file, { file_id: 'keep-file', filename: '同名.md' }]
        bindings.deleteSameNameFile(file)
        await globalThis.__knowledgeUploadDialogs[0].options.onOk()
        assert.deepEqual(deletes, [['shared', 'current-file']])
        assert.equal(bindings.sameNameFiles.value.length, 1)
        assert.equal(bindings.sameNameFiles.value[0].file_id, 'keep-file')
      } finally {
        app.unmount()
      }
    })

    await t.test('同名删除已发出后切会话，迟到响应不更新新同名列表', async () => {
      let resolveDelete
      const deletes = []
      globalThis.__knowledgeUploadApi = {
        deleteDocument: (...args) => {
          deletes.push(args)
          return new Promise((resolve) => (resolveDelete = resolve))
        }
      }
      globalThis.__knowledgeUploadDialogs = []
      const { bindings, app, user } = await mountUpload()
      try {
        const file = { file_id: 'old-file', filename: '同名.md' }
        bindings.sameNameFiles.value = [file]
        bindings.deleteSameNameFile(file)
        const dialog = globalThis.__knowledgeUploadDialogs[0]
        const pending = dialog.options.onOk()
        assert.deepEqual(deletes, [['shared', 'old-file']])
        user.token = 'new-session'
        bindings.sameNameFiles.value = [{ file_id: 'old-file', filename: '新会话文件.md' }]
        resolveDelete()
        await pending
        assert.equal(dialog.destroyed, 1)
        assert.equal(bindings.sameNameFiles.value[0].filename, '新会话文件.md')
      } finally {
        app.unmount()
      }
    })

    for (const mode of ['file', 'workspace', 'url']) {
      await t.test(`${mode} 入库迟到结果不发成功事件或覆盖新 loading`, async () => {
        globalThis.__knowledgeUploadApi = {
          importWorkspaceFiles: async () => ({ items: [{ file_path: 'minio://shared/old.md' }] })
        }
        const fixture = await mountUpload(mode === 'url' ? 'admin' : 'user')
        const { bindings, app, store, user, events } = fixture
        let resolveAdd
        let addStarted = false
        store.addFiles = () => {
          addStarted = true
          return new Promise((resolve) => (resolveAdd = resolve))
        }
        try {
          bindings.uploadMode.value = mode
          await nextTick()
          if (mode === 'workspace') {
            bindings.selectedWorkspacePaths.value = ['projects/old/readme.md']
          } else if (mode === 'url') {
            bindings.urlList.value = [
              { status: 'success', data: { file_path: 'minio://shared/old.md' } }
            ]
          } else {
            bindings.fileList.value = [
              { name: 'old.md', status: 'done', response: { file_path: 'minio://shared/old.md' } }
            ]
          }
          const pending = bindings.chunkData()
          await flush()
          assert.equal(addStarted, true)
          user.token = 'new-session'
          store.state.chunkLoading = true
          resolveAdd()
          await pending
          assert.deepEqual(events, [])
          assert.equal(store.state.chunkLoading, true)
        } finally {
          app.unmount()
        }
      })
    }
  } finally {
    globalThis.XMLHttpRequest = oldXhr
    delete globalThis.__knowledgeUploadUser
    delete globalThis.__knowledgeUploadStore
    delete globalThis.__knowledgeUploadApi
    delete globalThis.__knowledgeUploadDialogs
    unlinkSync(compiledPath)
    unlinkSync(dependencyPath)
  }
})
