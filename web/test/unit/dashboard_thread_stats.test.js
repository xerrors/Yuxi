import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

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
  const server = await createServer({
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
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
  const userStore = useUserStore()
  userStore.token = 'dashboard-test-token'
  userStore.userId = 1
  userStore.userRole = 'superadmin'
  return userStore
}

test('dashboardApi.getThreadStats 正确拼接时间范围与智能体过滤参数', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({
        summary: { total_threads: 10, active_threads: 5 },
        daily_trends: [],
        depth_distribution: {},
        agent_distribution: [],
        top_users: [],
        status_distribution: {}
      })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')

    const res1 = await dashboardApi.getThreadStats({ timeRange: '14days' })
    assert.equal(requests[0], '/api/dashboard/stats/threads?time_range=14days')
    assert.equal(res1.summary.total_threads, 10)

    const res2 = await dashboardApi.getThreadStats({ timeRange: '30days', agentId: 'agent-coder' })
    assert.equal(requests[1], '/api/dashboard/stats/threads?time_range=30days&agent_id=agent-coder')
    assert.equal(res2.summary.active_threads, 5)

    await dashboardApi.getThreadStats({ timeRange: '90days', includeSubagents: true })
    assert.equal(requests[2], '/api/dashboard/stats/threads?time_range=90days&include_subagents=true')
  })
})

test('dashboardApi.getAllStats 始终请求知识库统计', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    const responses = {
      '/api/dashboard/stats': { total_conversations: 3 },
      '/api/dashboard/stats/users': { total_users: 2 },
      '/api/dashboard/stats/tools': { total_calls: 4 },
      '/api/dashboard/stats/agents': { total_agents: 1 },
      '/api/dashboard/stats/knowledge': { total_databases: 5 }
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
    assert.deepEqual(result.knowledge, responses['/api/dashboard/stats/knowledge'])
  })
})

test('会话分析保持紧凑摘要、彩色排行、刷新 loading 与统一头像 fallback', () => {
  const source = readFileSync(
    new URL('../../src/components/dashboard/ThreadStatsComponent.vue', import.meta.url),
    'utf8'
  )
  const refreshButton = source.match(/<a-button[^>]*class="refresh-btn"[\s\S]*?<\/a-button>/)?.[0]
  const summaryStart = source.indexOf('<DashboardMetricGrid class="thread-summary-grid">')
  const summaryEnd = source.indexOf('<!-- 筛选工具栏：位于指标卡与图表之间 -->')
  const summarySource = source.slice(summaryStart, summaryEnd)
  const agentChartStart = source.indexOf('const renderAgentChart')
  const agentChartEnd = source.indexOf('const handleResize')
  const agentChartSource = source.slice(agentChartStart, agentChartEnd)

  assert.ok(refreshButton)
  assert.ok(refreshButton.includes(':loading="loading"'))
  assert.match(source, /:default-src="generatePixelAvatar\(record\.agent_id\)"/)
  assert.match(source, /:default-src="generatePixelAvatar\(record\.uid\)"/)
  assert.match(source, /label: '全部', value: true/)
  assert.match(source, /label: '仅主智能体', value: false/)
  assert.match(source, /@change="changeSubagentScope"/)
  assert.doesNotMatch(source, /includeSubagents \? '包含' : '不含'/)
  assert.equal(summarySource.includes('#meta'), false)
  assert.equal((summarySource.match(/<DashboardMetricCard/g) || []).length, 4)
  assert.equal(summarySource.includes('Token'), false)
  assert.match(agentChartSource, /color:\s*\(params\)\s*=>\s*getColorByIndex\(params\.dataIndex\)/)
})

test('智能体分析不再渲染 TOP 5 排行且保留分布图', () => {
  const source = readFileSync(
    new URL('../../src/components/dashboard/AgentStatsComponent.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /对话\/工具调用分布 \(TOP 3\)/)
  assert.doesNotMatch(source, /表现最佳智能体|top_performing_agents|topPerformers|performerColumns/)
  assert.doesNotMatch(source, /<a-table/)
})

test('会话统计源码包含筛选请求代次和 loading 回写守卫', () => {
  const source = readFileSync(
    new URL('../../src/components/dashboard/ThreadStatsComponent.vue', import.meta.url),
    'utf8'
  )
  const statsLoader = source.slice(source.indexOf('const loadData'), source.indexOf('const changeSubagentScope'))
  const conversationLoader = source.slice(
    source.indexOf('const loadConversations'),
    source.indexOf('const resetFilters')
  )

  assert.match(statsLoader, /const requestId = \+\+latestStatsRequest/)
  assert.match(statsLoader, /includeSubagents: requestedIncludeSubagents/)
  assert.match(statsLoader, /if \(requestId !== latestStatsRequest\) return/)
  assert.ok(
    statsLoader.indexOf('includeSubagents.value = requestedIncludeSubagents') >
      statsLoader.indexOf('await dashboardApi.getThreadStats')
  )
  assert.match(statsLoader, /if \(requestId === latestStatsRequest\) loading\.value = false/)
  assert.match(conversationLoader, /const requestId = \+\+latestConversationRequest/)
  assert.match(conversationLoader, /if \(requestId !== latestConversationRequest\) return/)
  assert.match(
    conversationLoader,
    /if \(requestId === latestConversationRequest\) tableLoading\.value = false/
  )
})

test('formatStorageSize 将容量限制为四位有效数字并分离单位', async () => {
  await withServer(async (server) => {
    const { formatStorageSize } = await server.ssrLoadModule('/src/utils/dashboard.js')

    assert.deepEqual(formatStorageSize(518.2 * 1024), { value: '518.2', unit: 'KB' })
    assert.deepEqual(formatStorageSize(12.345 * 1024 ** 3), { value: '12.35', unit: 'GB' })
    assert.deepEqual(formatStorageSize(10 * 1024 ** 5), { value: '10', unit: 'PB' })
    assert.deepEqual(formatStorageSize(0), { value: '0', unit: 'B' })
  })
})

test('buildHeatmapMonthSegments 忽略拥挤的残月并保留完整月份', async () => {
  await withServer(async (server) => {
    const { buildHeatmapMonthSegments } = await server.ssrLoadModule('/src/utils/dashboard.js')
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

test('dashboardApi.getConversationFilterOptions 请求会话审计筛选项', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({ users: [], agents: [] })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getConversationFilterOptions()

    assert.equal(requests[0], '/api/dashboard/conversations/options')
    assert.deepEqual(result, { users: [], agents: [] })
  })
})

test('dashboardApi.getConversations 正确拼接 search 搜索关键词与分页参数', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({
        items: [
          {
            thread_id: 'thread-123',
            title: 'Search match',
            status: 'active'
          }
        ],
        total: 41,
        limit: 20,
        offset: 40
      })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')

    const result = await dashboardApi.getConversations({
      status: 'active',
      search: 'search term',
      limit: 20,
      offset: 40
    })

    assert.equal(
      requests[0],
      '/api/dashboard/conversations?status=active&search=search+term&limit=20&offset=40'
    )
    assert.equal(result.total, 41)
    assert.equal(result.items.length, 1)
    assert.equal(result.items[0].thread_id, 'thread-123')
  })
})
