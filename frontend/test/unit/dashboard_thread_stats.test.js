import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

function jsonResponse(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  })
}

async function withServer(run) {
  const server = await createTestServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'test-message-api',
        enforce: 'pre',
        resolveId(id) {
          return id === 'ant-design-vue' ? '\0test-message-api' : null
        },
        load(id) {
          if (id !== '\0test-message-api') return null
          return 'export const message = { error() {}, success() {}, warning() {} }'
        }
      }
    ]
  })

  try {
    await run(server)
  } finally {
    await server.close()
  }
}

async function prepareStores(server) {
  setActivePinia(createPinia())
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const userStore = useUserStore()
  userStore.token = 'dashboard-test-token'
  userStore.userId = 1
  userStore.userRole = 'superadmin'
  return userStore
}

test('dashboardApi.getAllStats 始终请求知识库统计', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    const responses = {
      '/api/dashboard/stats': { total_sessions: 3 },
      '/api/dashboard/stats/users': { total_users: 2 },
      '/api/dashboard/stats/tools': { total_calls: 4 },
      '/api/dashboard/stats/knowledge': { total_knowledge_bases: 5 }
    }
    globalThis.fetch = async (input) => {
      const url = String(input)
      requests.push(url)
      return jsonResponse(responses[url])
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getAllStats()

    assert.deepEqual(requests, Object.keys(responses))
    assert.deepEqual(result, {
      basic: responses['/api/dashboard/stats'],
      users: responses['/api/dashboard/stats/users'],
      tools: responses['/api/dashboard/stats/tools'],
      knowledge: responses['/api/dashboard/stats/knowledge']
    })
  })
})

test('dashboardApi.getAllStats 在单个统计失败时保留其余结果', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const responses = {
      '/api/dashboard/stats': { total_sessions: 3 },
      '/api/dashboard/stats/users': { total_users: 2 },
      '/api/dashboard/stats/tools': { total_calls: 4 }
    }
    globalThis.fetch = async (input) => {
      const url = String(input)
      if (url.endsWith('/api/dashboard/stats/knowledge')) {
        return new Response(JSON.stringify({ detail: 'missing' }), {
          status: 404,
          headers: { 'content-type': 'application/json' }
        })
      }
      return jsonResponse(responses[url])
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getAllStats()

    assert.deepEqual(result.basic, responses['/api/dashboard/stats'])
    assert.deepEqual(result.users, responses['/api/dashboard/stats/users'])
    assert.deepEqual(result.tools, responses['/api/dashboard/stats/tools'])
    assert.equal(result.knowledge, null)
  })
})

test('formatStatNumber 在统计缺失时不用 0 占位', async () => {
  const { formatStatNumber } = await import('../../src/modules/dashboard/model/dashboard.js')
  assert.equal(formatStatNumber(null, 3), '—')
  assert.equal(formatStatNumber({ total: 0 }, 0), '0')
})

test('formatStorageSize 将容量限制为四位有效数字并分离单位', async () => {
  await withServer(async (server) => {
    const { formatStorageSize } = await server.ssrLoadModule(
      '/src/modules/dashboard/model/dashboard.js'
    )

    assert.deepEqual(formatStorageSize(518.2 * 1024), { value: '518.2', unit: 'KB' })
    assert.deepEqual(formatStorageSize(12.345 * 1024 ** 3), { value: '12.35', unit: 'GB' })
    assert.deepEqual(formatStorageSize(10 * 1024 ** 5), { value: '10', unit: 'PB' })
    assert.deepEqual(formatStorageSize(0), { value: '0', unit: 'B' })
  })
})

test('buildHeatmapMonthSegments 忽略拥挤的残月并保留完整月份', async () => {
  await withServer(async (server) => {
    const { buildHeatmapMonthSegments } = await server.ssrLoadModule(
      '/src/modules/dashboard/model/dashboard.js'
    )
    const weeks = [
      [{ date: '2026-04-27' }],
      [{ date: '2026-05-04' }],
      [{ date: '2026-05-11' }],
      [{ date: '2026-06-01' }],
      [{ date: '2026-06-08' }]
    ]

    assert.deepEqual(buildHeatmapMonthSegments(weeks), [
      { key: '5月-1', label: '5月', start: 1, span: 2 },
      { key: '6月-3', label: '6月', start: 3, span: 2 }
    ])
  })
})

test('dashboardApi.getVmStats 独立请求执行节点实时计数', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({ active_vms: 2, total_vms: 7 })
    }
    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    assert.deepEqual(await dashboardApi.getVmStats(), { active_vms: 2, total_vms: 7 })
    assert.deepEqual(requests, ['/api/dashboard/stats/vms'])
  })
})

test('智能体会话分布独立查询四种范围', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({
        total_threads: 2,
        agent_count: 1,
        avg_messages_per_thread: 1.5,
        agent_distribution: []
      })
    }
    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    for (const range of ['all', '7days', '30days', '3months']) {
      const result = await dashboardApi.getAgentSessionDistribution(range)
      assert.equal(result.avg_messages_per_thread, 1.5)
    }
    assert.deepEqual(
      requests,
      ['all', '7days', '30days', '3months'].map(
        (range) => `/api/dashboard/stats/agents/sessions?time_range=${range}`
      )
    )
  })
})
