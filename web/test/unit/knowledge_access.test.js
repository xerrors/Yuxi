import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createApp } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createServer } from 'vite'
import { message, Modal } from 'ant-design-vue'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key)
}

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

async function withStores(t, run) {
  storageValues.clear()
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  const pinia = createPinia()
  const app = createApp({})
  app.use(pinia)
  app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
  setActivePinia(pinia)
  const api = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
  const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
  const user = useUserStore()
  user.token = 'member-session'
  user.userRole = 'user'
  const store = app.runWithContext(() => useDatabaseStore())
  t.mock.method(message, 'error', () => {})
  t.mock.method(console, 'error', () => {})
  try {
    await run({ api, user, store })
  } finally {
    store.$dispose()
    await server.close()
  }
}

test('普通用户文档与单库读取 API 发出登录请求，整库管理仍拒绝', async (t) => {
  await withStores(t, async ({ api }) => {
    const requests = []
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      requests.push({ url, options })
      return new Response(JSON.stringify({ status: 'success' }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    })
    await api.databaseApi.getDatabaseInfo('shared')
    await api.documentApi.listDocuments('shared')
    await api.documentApi.createFolder('shared', '资料')
    await api.documentApi.renameFolder('shared', 'folder', '说明')
    await api.documentApi.moveDocument('shared', 'doc', 'folder')
    await api.documentApi.addUploadedDocuments('shared', ['uploaded'])
    await api.documentApi.parseDocuments('shared', ['doc'])
    await api.documentApi.indexDocuments('shared', ['doc'])
    await api.documentApi.deleteDocument('shared', 'doc')
    await api.fileApi.uploadFile(new Blob(['资料']), 'shared')
    await api.fileApi.importWorkspaceFiles('shared', ['/documents/readme.md'])
    await api.fileApi.getSupportedFileTypes()
    await api.typeApi.getChunkPresets()
    await api.queryApi.queryTest('shared', '流程')
    await api.queryApi.getKnowledgeBaseQueryParams('shared')
    await api.queryApi.getSampleQuestions('shared')
    await api.graphBuildApi.getStatus('shared')
    await api.mindmapApi.getByDatabase('shared')
    await api.mindmapApi.getDiff('shared')
    assert.equal(requests.length, 19)
    assert.ok(
      requests.every(({ options }) => options.headers.Authorization === 'Bearer member-session')
    )
    assert.ok(
      requests.some(
        ({ url, options }) => url.endsWith('/documents/doc') && options.method === 'DELETE'
      )
    )
    assert.ok(
      requests.some(
        ({ url, options }) => url.endsWith('/documents/parse') && options.method === 'POST'
      )
    )
    assert.ok(
      requests.some(
        ({ url, options }) =>
          url.endsWith('/files/upload?kb_id=shared') && options.body instanceof FormData
      )
    )
    for (const action of [
      () => api.databaseApi.getDatabases(),
      () => api.fileApi.fetchUrl('https://example.test/guide', 'shared'),
      () => api.databaseApi.createDatabase({ name: '新的库' }),
      () => api.databaseApi.updateDatabase('shared', { name: '改名' }),
      () => api.databaseApi.deleteDatabase('shared'),
      () => api.databaseApi.repairDatabaseStats('shared'),
      () => api.databaseApi.startVirtualFolderMigration('shared'),
      () => api.queryApi.generateSampleQuestions('shared'),
      () => api.graphBuildApi.startIndex('shared'),
      () => api.mindmapApi.generateMindmap('shared'),
      () => api.evaluationApi.runEvaluation('shared', {})
    ])
      await assert.rejects(action, /权限/)
    assert.equal(requests.length, 19)
    const download = await api.documentApi.downloadDocument('shared', 'doc')
    assert.ok(download instanceof Response)
  })
})

test('普通用户列表调用 accessible，管理员列表保留管理接口', async (t) => {
  await withStores(t, async ({ api, user, store }) => {
    t.mock.method(api.databaseApi, 'getAccessibleDatabases', async () => ({
      databases: [{ kb_id: 'shared', name: '共享资料' }]
    }))
    t.mock.method(api.databaseApi, 'getDatabases', async () => ({
      databases: [{ kb_id: 'admin', name: '管理员资料' }]
    }))
    await store.loadDatabases()
    assert.deepEqual(
      store.databases.map(({ kb_id }) => kb_id),
      ['shared']
    )
    assert.equal(api.databaseApi.getDatabases.mock.callCount(), 0)
    user.userRole = 'admin'
    await store.loadDatabases()
    assert.deepEqual(
      store.databases.map(({ kb_id }) => kb_id),
      ['admin']
    )
    assert.equal(api.databaseApi.getDatabases.mock.callCount(), 1)
  })
})

test('失去单库授权后清空详情、文件与查询配置并停止轮询', async (t) => {
  await withStores(t, async ({ api, store }) => {
    store.kbId = 'shared'
    store.database = { kb_id: 'shared', can_edit_documents: true, files: { private: {} } }
    store.documentFiles = [{ file_id: 'private', filename: '旧资料' }]
    store.queryParams = [{ key: 'private' }]
    store.meta.private = 'old'
    store.openFileDetail('private')
    store.state.autoRefresh = true
    store.startAutoRefresh()
    t.mock.method(api.databaseApi, 'getDatabaseInfo', async () => {
      throw Object.assign(new Error('没有权限'), { status: 403 })
    })
    await store.getDatabaseInfo()
    assert.equal(store.database.kb_id, undefined)
    assert.deepEqual(store.documentFiles, [])
    assert.deepEqual(store.queryParams, [])
    assert.deepEqual(store.meta, {})
    assert.equal(store.state.fileDetailModalVisible, false)
    assert.equal(store.state.autoRefresh, false)
    assert.equal(store.state.databaseLoading, false)
  })
})

test('切换知识库即清空旧文档，旧库晚到响应不覆盖新库', async (t) => {
  await withStores(t, async ({ api, store }) => {
    const oldDetail = deferred()
    const oldFiles = deferred()
    t.mock.method(api.databaseApi, 'getDatabaseInfo', (id) =>
      id === 'old'
        ? oldDetail.promise
        : Promise.resolve({ kb_id: 'new', can_edit_documents: false })
    )
    t.mock.method(api.documentApi, 'listDocuments', () => oldFiles.promise)
    store.kbId = 'old'
    store.database = { kb_id: 'old', files: { secret: {} }, can_edit_documents: true }
    store.documentFiles = [{ file_id: 'secret' }]
    const detailRequest = store.getDatabaseInfo('old', true)
    const fileRequest = store.loadDocumentFiles()
    await store.getDatabaseInfo('new', true)
    assert.equal(store.database.kb_id, 'new')
    assert.deepEqual(store.database.files, {})
    assert.deepEqual(store.documentFiles, [])
    oldDetail.resolve({ kb_id: 'old', can_edit_documents: true })
    oldFiles.resolve({ items: [{ file_id: 'secret' }] })
    await Promise.all([detailRequest, fileRequest])
    assert.equal(store.database.kb_id, 'new')
    assert.equal(store.database.can_edit_documents, false)
    assert.deepEqual(store.documentFiles, [])
  })
})

test('账号切换同步清空旧资产，并拒绝晚到列表、详情、文件和查询参数', async (t) => {
  await withStores(t, async ({ api, user, store }) => {
    const list = deferred()
    const detail = deferred()
    const files = deferred()
    const query = deferred()
    t.mock.method(api.databaseApi, 'getAccessibleDatabases', () => list.promise)
    t.mock.method(api.databaseApi, 'getDatabaseInfo', () => detail.promise)
    t.mock.method(api.documentApi, 'listDocuments', () => files.promise)
    t.mock.method(api.queryApi, 'getKnowledgeBaseQueryParams', () => query.promise)
    store.kbId = 'old'
    store.database = { kb_id: 'old', can_manage: true }
    store.databases = [{ kb_id: 'old' }]
    const requests = [
      store.loadDatabases(),
      store.getDatabaseInfo('old', true),
      store.loadDocumentFiles(),
      store.loadQueryParams()
    ]
    user.token = 'new-account-session'
    assert.equal(store.kbId, null)
    assert.deepEqual(store.databases, [])
    assert.equal(store.database.kb_id, undefined)
    list.resolve({ databases: [{ kb_id: 'old' }] })
    detail.resolve({ kb_id: 'old', can_manage: true })
    files.resolve({ items: [{ file_id: 'old-secret' }] })
    query.resolve({ params: { options: [{ key: 'old-secret', default: 'secret' }] } })
    await Promise.all(requests)
    assert.deepEqual(store.databases, [])
    assert.equal(store.database.kb_id, undefined)
    assert.deepEqual(store.documentFiles, [])
    assert.deepEqual(store.queryParams, [])
    assert.deepEqual(store.meta, {})
  })
})

test('文档删除确认在切库、切账号或失去编辑权时关闭，旧确认不删除新库文件', async (t) => {
  await withStores(t, async ({ api, user, store }) => {
    const dialogs = []
    const deletions = []
    t.mock.method(Modal, 'confirm', (options) => {
      const dialog = {
        options,
        destroyed: false,
        destroy() {
          this.destroyed = true
        }
      }
      dialogs.push(dialog)
      return dialog
    })
    t.mock.method(api.documentApi, 'deleteDocument', async (...args) => deletions.push(args))
    t.mock.method(api.documentApi, 'batchDeleteDocuments', async (...args) => {
      deletions.push(args)
      return { deleted_count: 1 }
    })
    for (const change of ['kb', 'session', 'permission']) {
      store.kbId = 'old-kb'
      store.database = {
        kb_id: 'old-kb',
        can_edit_documents: true,
        files: { old: { file_id: 'old', filename: '旧资料.md', status: 'uploaded' } }
      }
      store.selectedRowKeys = ['old']
      store.handleDeleteFile('old')
      store.handleBatchDelete()
      const currentDialogs = dialogs.slice(-2)
      assert.equal(currentDialogs.length, 2)
      if (change === 'kb') store.kbId = 'new-kb'
      if (change === 'session') user.token = 'new-session'
      if (change === 'permission') store.database.can_edit_documents = false
      assert.ok(currentDialogs.every((dialog) => dialog.destroyed))
      for (const dialog of currentDialogs) await dialog.options.onOk()
      assert.deepEqual(deletions, [])
    }
  })
})

test('权限失败、撤回恢复或切库返回后，旧写响应仍不能恢复轮询或清新 loading', async (t) => {
  await withStores(t, async ({ api, store }) => {
    t.mock.method(message, 'success', () => {})
    for (const change of ['permission-cycle', '403', 'kb-return']) {
      store.kbId = 'shared'
      store.database = { kb_id: 'shared', can_edit_documents: true }
      const late = deferred()
      t.mock.method(api.documentApi, 'parseDocuments', () => late.promise)
      const oldRequest = store.parseFiles(['old-file'])
      if (change === '403') {
        t.mock.method(api.databaseApi, 'getDatabaseInfo', async () => {
          throw Object.assign(new Error('没有权限'), { status: 403 })
        })
        await store.getDatabaseInfo('shared', true)
        assert.equal(store.database.kb_id, undefined)
        store.database = { kb_id: 'shared', can_edit_documents: true }
      } else if (change === 'kb-return') {
        store.kbId = 'other'
        store.kbId = 'shared'
        store.database = { kb_id: 'shared', can_edit_documents: true }
      } else {
        store.database.can_edit_documents = false
        store.database.can_edit_documents = true
      }
      store.state.chunkLoading = true
      late.resolve({ status: 'queued', task_id: 'old-task' })
      assert.equal(await oldRequest, undefined)
      assert.equal(store.state.autoRefresh, false)
      assert.equal(store.state.chunkLoading, true)
    }
  })
})
