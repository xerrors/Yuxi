import test from 'node:test'
import assert from 'node:assert/strict'

import { createPinia, setActivePinia } from 'pinia'
import { createApp } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createServer } from 'vite'
import { message } from 'ant-design-vue'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

test('从二级目录点击全部文件会清空 parent_id 并返回根目录', async (t) => {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })

  try {
    const warnings = []
    t.mock.method(console, 'warn', (...args) => warnings.push(args))
    const pinia = createPinia()
    const app = createApp({})
    app.use(pinia)
    app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
    setActivePinia(pinia)
    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
    const requests = []

    documentApi.listDocuments = async (kbId, params) => {
      requests.push({ kbId, params })
      return {
        items: [],
        page: 1,
        page_size: 100,
        total: 0,
        has_more: false,
        path_prefix: ''
      }
    }

    const store = app.runWithContext(() => useDatabaseStore())
    store.kbId = 'kb_1'
    store.fileBrowser.parentId = 'folder_2'
    store.folderBreadcrumbs = [
      { file_id: null, filename: '全部文件', path_prefix: '' },
      { file_id: 'folder_2', filename: '二级目录', path_prefix: '' }
    ]

    await store.goToFolder(0)

    assert.equal(store.fileBrowser.parentId, null)
    assert.deepEqual(store.folderBreadcrumbs, [
      { file_id: null, filename: '全部文件', path_prefix: '' }
    ])
    assert.deepEqual(requests, [
      {
        kbId: 'kb_1',
        params: {
          page: 1,
          page_size: 100,
          status: 'all',
          recursive: false
        }
      }
    ])
    assert.deepEqual(warnings, [])
  } finally {
    await server.close()
  }
})

test('知识库提交跨账号返回时，不把旧入队任务登记给新账号', async (t) => {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  const pinia = createPinia()
  const app = createApp({})
  app.use(pinia)
  app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
  setActivePinia(pinia)
  const { documentApi, databaseApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
  const { useTaskerStore } = await server.ssrLoadModule('/src/stores/tasker.js')
  const database = app.runWithContext(() => useDatabaseStore())
  const user = useUserStore()
  const tasker = useTaskerStore()
  t.mock.method(message, 'success', () => {})
  t.mock.method(databaseApi, 'getDatabaseInfo', async () => ({
    kb_id: database.kbId,
    can_edit_documents: true,
    stats: { processing_count: 0 }
  }))
  t.mock.method(documentApi, 'listDocuments', async () => ({
    items: [],
    stats: { processing_count: 0 }
  }))
  try {
    for (const [action, apiMethod, args] of [
      ['addFiles', 'addDocuments', [{ items: ['fixture'], contentType: 'file', params: {} }]],
      ['parseFiles', 'parseDocuments', [['file']]],
      ['parsePendingFiles', 'parsePendingDocuments', []],
      ['indexFiles', 'indexDocuments', [['file']]],
      ['indexPendingFiles', 'indexPendingDocuments', []]
    ]) {
      await t.test(action, async () => {
        user.token = `old-${action}`
        user.userRole = 'admin'
        database.kbId = 'test-kb'
        database.database = { kb_id: 'test-kb', can_edit_documents: true }
        let resolve
        t.mock.method(
          documentApi,
          apiMethod,
          () =>
            new Promise((done) => {
              resolve = done
            })
        )
        const pending = database[action](...args)
        user.logout()
        user.token = `new-${action}`
        user.userRole = 'admin'
        database.kbId = 'new-kb'
        database.database = { kb_id: 'new-kb', can_edit_documents: true }
        database.state.chunkLoading = true
        resolve({ status: 'queued', task_id: 'old-task' })
        assert.equal(await pending, undefined)
        assert.deepEqual(tasker.tasks, [])
        assert.equal(database.state.chunkLoading, true)
        assert.equal(database.state.autoRefresh, false)
        const current = database[action](...args)
        resolve({ status: 'queued', task_id: `new-${action}` })
        assert.equal(await current, true)
        assert.equal(tasker.tasks[0].id, `new-${action}`)
        tasker.reset()
      })
    }
  } finally {
    database.stopAutoRefresh()
    tasker.$dispose()
    await server.close()
  }
})
