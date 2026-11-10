import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'
import { createRenderer, createSSRApp, h, reactive, ssrContextKey } from 'vue'
import { renderToString } from 'vue/server-renderer'

const deferred = () => {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('后台任务抽屉为文档产物撤销展示中文类型', async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'drawer-boundary', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue') return '\0drawer-ant' },
    load(id) { if (id === '\0drawer-ant') return 'export const Modal = {}' }
  }] })
  try {
    const { default: component } = await server.ssrLoadModule('/src/modules/background-jobs/ui/BackgroundJobsDrawer.vue')
    const { useBackgroundJobsStore } = await server.ssrLoadModule('/src/modules/background-jobs/model/jobs.js')
    const app = createSSRApp({ setup() {
      const state = component.setup({}, { expose() {}, emit() {} })
      assert.equal(state.jobTypeLabel('knowledge_document_reset'), '文档产物撤销')
      assert.equal(state.jobTypeLabel('knowledge_graph_index'), '图谱构建')
      assert.equal(state.isFailedStatus('failed'), true)
      assert.equal(state.isFailedStatus('cancelled'), false)
      const store = useBackgroundJobsStore()
      store.jobs = [
        { id: 'cancelled-job', status: 'cancelled', name: '已取消', created_at: '2026-01-01T00:00:00Z' },
        { id: 'failed-job', status: 'failed', name: '失败', created_at: '2026-01-02T00:00:00Z' }
      ]
      state.statusFilter.value = 'failed'
      assert.deepEqual(state.filteredJobs.value.map((job) => job.id), ['failed-job'])
      assert.equal(state.emptyHint.value.subtitle, '执行失败的后台作业会显示在这里。')
      state.statusFilter.value = 'all'
      assert.equal(state.filteredJobs.value.length, 2)
      return () => h('div')
    } })
    app.use(createPinia())
    await renderToString(app)
  } finally { await server.close(); delete globalThis.localStorage }
})

const node = () => ({ children: [], parent: null })
const renderer = createRenderer({
  createElement: node, createText: node, createComment: node,
  insert(child, parent) { child.parent = parent; parent.children.push(child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText() {}, setElementText() {}, parentNode: (n) => n.parent, nextSibling: () => null, patchProp() {}
})

test('任务请求时序与轮询生命周期', async (t) => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom', ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
      name: 'graph-job-boundaries', enforce: 'pre',
      resolveId(id) { if (id === 'ant-design-vue') return '\0jobs-ant' },
      load(id) {
        if (id === '\0jobs-ant') return 'export const message = { success() {}, error: (...args) => globalThis.__graphJob.error(...args) }; export const Modal = {}'
        if (id.endsWith('/src/modules/knowledge/model/knowledgeBase.js')) return 'export const useKnowledgeBaseStore = () => ({ kbId: "test-kb", knowledgeBase: { kb_type: "milvus" } })'
        if (id.endsWith('/src/modules/background-jobs/index.js')) return 'export const useBackgroundJobsStore = () => globalThis.__graphJob.store'
        if (id.endsWith('/src/modules/settings/index.js')) return 'export const useConfigStore = () => ({})'
        if (id.endsWith('/src/apis/graph_api.js')) return 'export const graphApi = {}'
        if (id.endsWith('/src/apis/knowledge_api.js')) return 'export const documentApi = {}; export const graphBuildApi = new Proxy({}, { get: (_, k) => globalThis.__graphJob.api[k] })'
        if (['GraphCanvas.vue', 'FileSearchModal.vue', 'GraphDetailPanel.vue', 'ResourceEmptyState.vue', 'ModelSelectorComponent.vue'].some((name) => id.endsWith('/' + name))) return '<script>export default {}</script>'
      }
    }]
  })
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const { useBackgroundJobsStore } = await server.ssrLoadModule(
    '/src/modules/background-jobs/model/jobs.js'
  )
  const { backgroundJobsApi } = await server.ssrLoadModule('/src/apis/background_jobs.js')
  const stores = []
  const makeStore = () => {
    setActivePinia(createPinia())
    const user = useUserStore()
    user.token = 'test-session'
    user.userRole = 'admin'
    const store = useBackgroundJobsStore()
    backgroundJobsApi.fetchJobDetail = async (id) => ({ job: { id, status: 'pending' } })
    stores.push(store)
    return { store, user }
  }
  try {
    await t.test('首次详情失败后，关闭抽屉仍重试权威列表并读取已完成任务', async (t) => {
      const { store } = makeStore()
      t.mock.timers.enable({ apis: ['setTimeout'] })
      t.mock.method(console, 'error', () => {})
      backgroundJobsApi.fetchJobDetail = async () => {
        throw new Error('temporary disconnect')
      }
      let calls = 0
      backgroundJobsApi.fetchJobs = async () => {
        calls++
        return { jobs: [{ id: 'receipt-job', status: 'success', progress: 100 }] }
      }
      await store.createJobRegistration()({ job_id: 'receipt-job' })
      assert.deepEqual(store.jobs, [])
      assert.equal(store.isDrawerOpen, false)
      t.mock.timers.tick(5000)
      await Promise.resolve()
      assert.equal(calls, 1)
      assert.equal(store.jobs[0].status, 'success')
      t.mock.timers.tick(30000)
      assert.equal(calls, 1)
      store.reset()
    })
    await t.test('图谱构建和重试的旧提交回执不能进入新会话', async () => {
      const { default: component } = await server.ssrLoadModule('/src/modules/knowledge/ui/KnowledgeGraphSection.vue')
      for (const [name, apiMethod] of [
        ['startGraphBuild', 'startIndex'],
        ['retryGraphVectors', 'reconcile']
      ]) {
        const { store, user } = makeStore()
        const response = deferred()
        globalThis.__graphJob = { store, api: { [apiMethod]: () => response.promise, getStatus: async () => ({ locked: false }) }, error: assert.fail }
        let state
        const app = renderer.createApp({ setup() {
          state = component.setup(reactive({ active: false, readonly: false, fileId: null }), { expose() {}, emit() {} })
          return () => h('div')
        } })
        app.provide(ssrContextKey, { modules: new Set() })
        app.mount(node())
        const action = state[name]
        const pending = action()
        user.logout()
        user.token = 'next-session'
        user.userRole = 'admin'
        response.resolve({ job_id: 'old-graph-job' })
        await pending
        assert.deepEqual(store.jobs, [])
        await action()
        assert.equal(store.jobs[0].id, 'old-graph-job')
        app.unmount()
        delete globalThis.__graphJob
        store.reset()
      }
    })
    await t.test('旧提交回执不能登记到新会话，新提交仍可登记', async () => {
      const { store, user } = makeStore()
      const registerOldJob = store.createJobRegistration()
      user.logout()
      user.token = 'next-session'
      user.userRole = 'admin'
      registerOldJob({ job_id: 'old-job' })
      assert.deepEqual(store.jobs, [])
      await store.createJobRegistration()({ job_id: 'new-job' })
      assert.equal(store.jobs[0].id, 'new-job')
      store.reset()
    })
    await t.test('隐藏页面跳过轮询，恢复可见后继续读取', async (t) => {
      const { store } = makeStore()
      t.mock.timers.enable({ apis: ['setTimeout'] })
      globalThis.document = { visibilityState: 'hidden' }
      let calls = 0
      backgroundJobsApi.fetchJobs = async () => {
        calls++
        return { jobs: [] }
      }
      try {
        store.openDrawer()
        t.mock.timers.tick(10000)
        assert.equal(calls, 0)
        document.visibilityState = 'visible'
        t.mock.timers.tick(5000)
        assert.equal(calls, 1)
        store.reset()
      } finally {
        delete globalThis.document
      }
    })
    await t.test('已入队任务不会被更早开始的列表抹去', async () => {
      const { store } = makeStore()
      const response = deferred()
      backgroundJobsApi.fetchJobs = () => response.promise
      const loading = store.loadJobs()
      await store.createJobRegistration()({ job_id: 'new-job' })
      response.resolve({ jobs: [] })
      await loading
      assert.equal(store.jobs[0].id, 'new-job')
      assert.equal(store.loading, false)
      store.reset()
    })
    await t.test('退出登录后详情请求不能回写，晚到旧详情不能覆盖新详情', async () => {
      const { store, user } = makeStore()
      const old = deferred(),
        latest = deferred()
      let calls = 0
      backgroundJobsApi.fetchJobDetail = () => (++calls === 1 ? old.promise : latest.promise)
      const first = store.refreshJob('job'),
        second = store.refreshJob('job')
      latest.resolve({ job: { id: 'job', status: 'success' } })
      await second
      old.resolve({ job: { id: 'job', status: 'running' } })
      await first
      assert.equal(store.jobs[0].status, 'success')
      const afterLogout = deferred()
      backgroundJobsApi.fetchJobDetail = () => afterLogout.promise
      const pending = store.refreshJob('job')
      user.logout()
      afterLogout.resolve({ job: { id: 'job', status: 'running' } })
      await pending
      assert.deepEqual(store.jobs, [])
    })
    await t.test('详情更新后旧列表不能恢复过期内容', async () => {
      const { store } = makeStore()
      const response = deferred()
      backgroundJobsApi.fetchJobs = () => response.promise
      backgroundJobsApi.fetchJobDetail = async () => ({ job: { id: 'job', status: 'success' } })
      const loading = store.loadJobs()
      await store.refreshJob('job')
      response.resolve({ jobs: [{ id: 'job', status: 'running' }] })
      await loading
      assert.equal(store.jobs[0].status, 'success')
      store.reset()
    })
    await t.test('旧详情响应不能覆盖较新的列表终态', async () => {
      const { store } = makeStore()
      const response = deferred()
      backgroundJobsApi.fetchJobDetail = () => response.promise
      backgroundJobsApi.fetchJobs = async () => ({ jobs: [{ id: 'job', status: 'success' }] })
      const pending = store.refreshJob('job')
      await store.loadJobs()
      response.resolve({ job: { id: 'job', status: 'running' } })
      await pending
      assert.equal(store.jobs[0].status, 'success')
      store.reset()
    })
    await t.test('切换账号后旧取消与删除回调不操作新会话', async () => {
      const { store, user } = makeStore()
      const cancelled = deferred(),
        deleted = deferred()
      backgroundJobsApi.cancelJob = () => cancelled.promise
      backgroundJobsApi.deleteJob = () => deleted.promise
      backgroundJobsApi.fetchJobDetail = () => assert.fail('旧会话不能启动详情读取')
      const cancel = store.cancelJob('job'),
        remove = store.deleteJob('job')
      user.logout()
      user.token = 'next-session'
      user.userRole = 'admin'
      backgroundJobsApi.fetchJobDetail = async () => ({ job: { id: 'job', status: 'pending' } })
      await store.createJobRegistration()({ job_id: 'job' })
      backgroundJobsApi.fetchJobDetail = () => assert.fail('旧会话不能启动详情读取')
      cancelled.resolve({})
      deleted.resolve({})
      await Promise.all([cancel, remove])
      assert.equal(store.jobs[0].status, 'pending')
      assert.equal(store.lastError, null)
      store.reset()
    })
    await t.test('请求失败保留最近成功状态并退避', async (t) => {
      const { store } = makeStore()
      t.mock.timers.enable({ apis: ['setTimeout'] })
      t.mock.method(console, 'error', () => {})
      backgroundJobsApi.fetchJobs = async () => ({
        jobs: [{ id: 'job', status: 'running' }],
        summary: { total: 1, status_counts: { running: 1 } }
      })
      await store.loadJobs()
      let calls = 0
      backgroundJobsApi.fetchJobs = async () => {
        calls++
        throw new Error('offline')
      }
      await store.loadJobs()
      assert.equal(store.activeCount, 1)
      assert.equal(store.jobs[0].id, 'job')
      assert.equal(store.lastError.message, 'offline')
      t.mock.timers.tick(9999)
      assert.equal(calls, 1)
      t.mock.timers.tick(1)
      assert.equal(calls, 2)
      store.reset()
    })
    await t.test('旧列表响应不能覆盖新终态', async () => {
      const { store } = makeStore()
      const old = deferred(),
        latest = deferred()
      let calls = 0
      backgroundJobsApi.fetchJobs = () => (++calls === 1 ? old.promise : latest.promise)
      const first = store.loadJobs(),
        second = store.loadJobs()
      latest.resolve({ jobs: [{ id: 't', status: 'success' }] })
      await second
      old.resolve({ jobs: [{ id: 't', status: 'running' }] })
      await first
      assert.equal(store.jobs[0].status, 'success')
      store.reset()
    })
    await t.test('退出登录使在途响应失效并立即清空状态', async () => {
      const { store, user } = makeStore()
      const response = deferred()
      backgroundJobsApi.fetchJobs = () => response.promise
      backgroundJobsApi.fetchJobDetail = () => response.promise
      store.createJobRegistration()({ job_id: 'receipt' })
      const loading = store.loadJobs()
      user.logout()
      response.resolve({ jobs: [{ id: 'private-job', status: 'running' }] })
      await loading
      assert.deepEqual(store.jobs, [])
      assert.equal(store.loading, false)
      store.reset()
    })
    await t.test('失败计数只含 failed，cancelled 仍留在列表', async () => {
      const { store } = makeStore()
      backgroundJobsApi.fetchJobs = async () => ({
        jobs: [
          { id: 'failed-job', status: 'failed' },
          { id: 'cancelled-job', status: 'cancelled' }
        ],
        summary: { total: 2, status_counts: { failed: 2, cancelled: 5, success: 1 } }
      })
      await store.loadJobs()
      assert.equal(store.failedCount, 2)
      assert.equal(store.jobs.some((job) => job.status === 'cancelled'), true)
      store.reset()
    })
    await t.test('慢请求期间不产生重叠轮询，完成后才安排下一轮', async (t) => {
      const { store } = makeStore()
      t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
      const response = deferred()
      let calls = 0
      backgroundJobsApi.fetchJobs = () => {
        calls++
        return response.promise
      }
      store.openDrawer()
      t.mock.timers.tick(5000)
      assert.equal(calls, 1)
      t.mock.timers.tick(15000)
      assert.equal(calls, 1)
      response.resolve({ jobs: [] })
      await Promise.resolve()
      await Promise.resolve()
      t.mock.timers.tick(4999)
      assert.equal(calls, 1)
      t.mock.timers.tick(1)
      assert.equal(calls, 2)
      store.reset()
      t.mock.timers.tick(10000)
      assert.equal(calls, 2)
    })
  } finally {
    stores.forEach((store) => store.$dispose())
    await server.close()
  }
})

test('已登记作业的终态通知覆盖分页遗漏、重复快照和账号失效', async (t) => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const { useBackgroundJobsStore } = await server.ssrLoadModule(
    '/src/modules/background-jobs/model/jobs.js'
  )
  const { backgroundJobsApi } = await server.ssrLoadModule('/src/apis/background_jobs.js')
  setActivePinia(createPinia())
  const user = useUserStore()
  user.token = 'completion-session'
  user.userRole = 'admin'
  const store = useBackgroundJobsStore()
  let status = 'running'
  backgroundJobsApi.fetchJobDetail = async (id) => ({ job: { id, status } })
  backgroundJobsApi.fetchJobs = async () => ({ jobs: [] })
  try {
    for (const terminal of ['success', 'failed', 'cancelled']) {
      await t.test(terminal, async () => {
        status = 'running'
        let completed = 0
        await store.createJobRegistration(() => {
          completed++
        })({ job_id: terminal })
        assert.equal(completed, 0)
        status = terminal
        await store.loadJobs()
        assert.equal(completed, 1)
        await store.refreshJob(terminal)
        await store.loadJobs()
        assert.equal(completed, 1)
        store.reset()
      })
    }
    let completed = 0
    status = 'success'
    await store.createJobRegistration(() => {
      completed++
    })({ job_id: 'already-finished' })
    assert.equal(completed, 1)
    status = 'running'
    await store.createJobRegistration(() => {
      completed++
    })({ job_id: 'old-account' })
    user.logout()
    user.token = 'another-account'
    user.userRole = 'admin'
    status = 'success'
    await store.refreshJob('old-account')
    assert.equal(completed, 1)
  } finally {
    store.$dispose()
    await server.close()
  }
})
