import test from 'node:test'
import assert from 'node:assert/strict'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

const messageStubPlugin = {
  name: 'test-message-api',
  enforce: 'pre',
  resolveId(id) {
    return id === 'ant-design-vue' ? '\0test-message-api' : null
  },
  load(id) {
    if (id !== '\0test-message-api') return null
    return `const noop = async () => {}
export const message = { success: noop, error: noop, warning: noop, destroy: noop }
export const Modal = { confirm: noop }`
  }
}

// 受控定时器：链式轮询经 setTimeout 调度，测试内手动逐轮触发，不依赖真实等待
const installFakeTimers = () => {
  const original = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout }
  const timers = new Map()
  let nextId = 1
  globalThis.setTimeout = (fn) => {
    const id = nextId++
    timers.set(id, fn)
    return id
  }
  globalThis.clearTimeout = (id) => timers.delete(id)
  const settle = () => new Promise((resolve) => original.setTimeout(resolve, 0))
  const flushTick = async () => {
    // 先让此前的 await 链（微任务）落定，再把当前挂起的一次性回调作为一轮触发；
    // 回调完成后新排的下一轮定时器留给下一次 flushTick
    await settle()
    const pending = [...timers.values()]
    timers.clear()
    for (const fn of pending) fn()
    await settle()
  }
  const restore = () => Object.assign(globalThis, original)
  return { flushTick, restore }
}

const setupStores = async (server, { role, processingCount = 0, fileStatus = 'uploaded' }) => {
  const { databaseApi, documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
  const { useTaskerStore } = await server.ssrLoadModule('/src/stores/tasker.js')
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')

  documentApi.indexDocuments = async () => ({
    status: 'success',
    task_id: 'task_q1',
    message: '入库任务已提交'
  })
  documentApi.listDocuments = async () => ({
    items: [{ file_id: 'f1', status: fileStatus }],
    page: 1,
    page_size: 100,
    total: 1,
    has_more: false,
    path_prefix: '',
    stats: { processing_count: processingCount, row_count: 1 }
  })
  databaseApi.getDatabaseInfo = async () => ({
    kb_id: 'kb_1',
    name: '测试库',
    files: {},
    stats: { processing_count: processingCount }
  })

  setActivePinia(createPinia())
  const store = useDatabaseStore()
  const tasker = useTaskerStore()
  const userStore = useUserStore()
  userStore.userRole = role
  store.kbId = 'kb_1'
  return { store, tasker }
}

test('admin：入库任务排队期间自动刷新不停止，任务终态后恢复停止', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [messageStubPlugin]
  })

  try {
    const { store, tasker } = await setupStores(server, { role: 'admin' })
    const timers = installFakeTimers()
    try {
      // indexFiles 内部 await delayedRefresh 的 1s 定时器，需要在等待期间排空
      const submit = store.indexFiles(['f1'])
      await timers.flushTick()
      await submit

      // worker 未领取时文件仍是 uploaded；任务在排队，自动刷新必须保持开启
      assert.equal(tasker.tasks[0]?.status, 'queued')
      assert.equal(
        store.state.autoRefresh,
        true,
        '排队中的入库任务不应导致文件列表自动刷新停止'
      )

      // admin 侧任务中心可轮询出终态，即使超过 stale 宽限也不得停
      // 8 轮 > AUTO_REFRESH_STALE_POLLS_LIMIT（当前为 6），若上调常量需同步加轮数
      for (let i = 0; i < 8; i += 1) await timers.flushTick()
      assert.equal(store.state.autoRefresh, true, '活跃任务存在时 admin 轮询不应被 stale 兜底停止')

      // 任务进入终态后恢复停止条件，避免无限轮询
      tasker.tasks[0].status = 'success'
      await store.loadDocumentFiles({ isBackground: true })
      assert.equal(store.state.autoRefresh, false)
    } finally {
      tasker.reset()
      timers.restore()
    }
  } finally {
    await server.close()
  }
})

test('非 admin：任务中心无本地登记事实，uploaded 间隙维持基线停止行为', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [messageStubPlugin]
  })

  try {
    const { store } = await setupStores(server, { role: 'user' })
    const timers = installFakeTimers()
    try {
      const submit = store.indexFiles(['f1'])
      await timers.flushTick()
      await submit
      // 非 admin 的 createTaskRegistration 被 gate 拒绝，没有可观察的任务事实；
      // 守卫不得为其延长轮询（钉住"两处 isAdmin 短路 = 零行为变化"的边界）。
      await timers.flushTick()
      assert.equal(store.state.autoRefresh, false, '非 admin 不应因守卫保持轮询')
    } finally {
      timers.restore()
    }
  } finally {
    await server.close()
  }
})
