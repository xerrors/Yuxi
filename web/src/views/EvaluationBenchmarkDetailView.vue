<template>
  <div class="benchmark-detail-container">
    <ExtensionDetailLayout
      :active-key="activeTab"
      :tabs="tabs"
      @update:active-key="handleActiveTabChange"
      :loading="loading"
      :ready="!!dataset && isCurrentDatabaseLoaded"
      empty-description="未找到评估基准"
      class="benchmark-detail-layout"
    >
      <template #breadcrumb>
        <nav class="extension-detail-breadcrumb benchmark-breadcrumb" aria-label="评估基准详情导航">
          <button type="button" class="extension-detail-back" @click="backToKnowledgeList">知识库</button>
          <ChevronRight :size="15" aria-hidden="true" />
          <button type="button" class="extension-detail-back" @click="backToKnowledgeEvaluation">
            {{ database.name || kbId }}
          </button>
          <ChevronRight :size="15" aria-hidden="true" />
          <span class="extension-detail-current" :title="dataset?.name || datasetId">
            {{ dataset?.name || datasetId }}
          </span>
        </nav>
      </template>


      <template #panel-questions>
        <div class="benchmark-detail-panel">
          <div class="benchmark-summary-strip">
            <div class="benchmark-summary-main">
              <strong>{{ dataset.name }}</strong>
              <span>{{ dataset.description || '暂无描述' }}</span>
            </div>
            <div class="benchmark-summary-metrics">
              <span><small>题目</small><strong>{{ questionPagination.total }}</strong></span>
              <span><small>Gold Chunks</small><strong>{{ dataset.has_gold_chunks ? '有' : '无' }}</strong></span>
              <span><small>Gold Answer</small><strong>{{ dataset.has_gold_answers ? '有' : '无' }}</strong></span>
            </div>
          </div>

          <div class="benchmark-table-toolbar">
            <div class="benchmark-filter-group">
              <a-input
                v-model:value="questionKeyword"
                allow-clear
                class="benchmark-search-input"
                placeholder="筛选当前页题目"
                :prefix="h(Search, { size: 14 })"
              />
              <a-select
                v-model:value="questionAnnotationFilter"
                class="benchmark-filter-select"
                :options="annotationFilterOptions"
                aria-label="按标注类型筛选当前页"
              />
              <span class="benchmark-filter-note">筛选仅作用于当前页</span>
            </div>
            <div class="benchmark-toolbar-actions">
              <button
                type="button"
                class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
                @click="downloadDataset"
              >
                <Download :size="14" aria-hidden="true" />
                <span>下载</span>
              </button>
              <a-tooltip :title="questionAutoWrap ? '关闭自动换行' : '开启自动换行'">
                <a-button
                  class="benchmark-wrap-button"
                  :type="questionAutoWrap ? 'primary' : 'default'"
                  size="small"
                  :aria-label="questionAutoWrap ? '关闭自动换行' : '开启自动换行'"
                  @click="questionAutoWrap = !questionAutoWrap"
                >
                  <WrapText :size="14" aria-hidden="true" />
                </a-button>
              </a-tooltip>
            </div>
          </div>

          <div class="benchmark-table-area">
            <a-table
              :columns="questionColumns"
              :data-source="filteredQuestions"
              :pagination="questionPaginationConfig"
              :loading="questionsLoading"
              :scroll="{ x: 1080 }"
              :class="{ 'benchmark-table-nowrap': !questionAutoWrap }"
              row-key="item_id"
              size="small"
            >
              <template #bodyCell="{ column, record, index }">
                <template v-if="column.key === 'index'">
                  <span class="benchmark-index">
                    {{ (questionPagination.current - 1) * questionPagination.pageSize + index + 1 }}
                  </span>
                </template>
                <template v-else-if="column.key === 'query'">
                  <div class="benchmark-cell-primary" :title="record.query">{{ record.query }}</div>
                </template>
                <template v-else-if="column.key === 'gold_chunk_ids'">
                  <div v-if="record.gold_chunk_ids?.length" class="benchmark-cell-code">
                    {{ record.gold_chunk_ids.join(', ') }}
                  </div>
                  <span v-else class="benchmark-cell-empty">-</span>
                </template>
                <template v-else-if="column.key === 'gold_answer'">
                  <div v-if="record.gold_answer" class="benchmark-cell-secondary" :title="record.gold_answer">
                    {{ record.gold_answer }}
                  </div>
                  <span v-else class="benchmark-cell-empty">-</span>
                </template>
              </template>
              <template #emptyText>
                <a-empty
                  :description="questions.length > 0 ? '当前页没有符合筛选条件的题目' : '暂无题目'"
                />
              </template>
            </a-table>
          </div>
        </div>
      </template>

      <template #panel-results>
        <div class="benchmark-detail-panel">
          <ResourceEmptyState
            v-if="!runsLoading && datasetRuns.length === 0"
            class="benchmark-result-empty"
            title="暂无关联评估"
            description="从知识库评估页选择当前基准新建评估，运行记录会展示在这里。"
            :icon="BarChart3"
          >
            <template #actions>
              <a-button type="primary" @click="backToKnowledgeEvaluation">返回评估工作台</a-button>
            </template>
          </ResourceEmptyState>

          <template v-else>
            <div class="benchmark-result-summary">
              <div class="benchmark-run-identity">
                <a-select
                  id="benchmark-run-select"
                  v-model:value="selectedRunId"
                  class="benchmark-run-select"
                  :options="runOptions"
                  :loading="runsLoading"
                  :disabled="datasetRuns.length === 0"
                  :bordered="false"
                  @change="handleRunSelection"
                />
                <div class="benchmark-run-meta">
                  <span>{{ formatTime(selectedRun?.started_at) }} · {{ formatRunDuration(selectedRun) }}</span>
                </div>
                <span class="benchmark-run-status" :class="`status-${selectedRun?.status || 'unknown'}`">
                  {{ getRunStatusText(selectedRun?.status) }}
                </span>
              </div>
              <div class="benchmark-result-metrics">
                <span><small>综合评分</small><strong>{{ formatScore(selectedRun?.overall_score) }}</strong></span>
                <span><small>Recall@10</small><strong>{{ formatMetric(selectedRun?.metrics?.['recall@10']) }}</strong></span>
                <span><small>完成题目</small><strong>{{ formatRunItems(selectedRun) }}</strong></span>
              </div>
            </div>

            <div class="benchmark-table-toolbar">
              <div class="benchmark-filter-group">
                <a-input
                  v-model:value="resultKeyword"
                  allow-clear
                  class="benchmark-search-input"
                  placeholder="筛选当前页问题或答案"
                  :prefix="h(Search, { size: 14 })"
                />
                <a-select
                  v-model:value="resultFilter"
                  class="benchmark-filter-select"
                  :options="resultFilterOptions"
                  aria-label="评估结果筛选"
                  :suffix-icon="h(ListFilter, { size: 14 })"
                  @change="handleResultFilterChange"
                />
              </div>
              <a-tooltip :title="resultAutoWrap ? '关闭自动换行' : '开启自动换行'">
                <a-button
                  class="benchmark-wrap-button"
                  :type="resultAutoWrap ? 'primary' : 'default'"
                  size="small"
                  :aria-label="resultAutoWrap ? '关闭自动换行' : '开启自动换行'"
                  @click="resultAutoWrap = !resultAutoWrap"
                >
                  <WrapText :size="14" aria-hidden="true" />
                </a-button>
              </a-tooltip>
            </div>

            <div class="benchmark-table-area">
              <a-table
                :columns="resultColumns"
                :data-source="filteredResults"
                :pagination="resultPaginationConfig"
                :loading="resultsLoading"
                :scroll="{ x: 1390 }"
                :class="{ 'benchmark-table-nowrap': !resultAutoWrap }"
                :row-key="getResultRowKey"
                size="small"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'query'">
                    <div class="benchmark-cell-primary" :title="record.query">{{ record.query }}</div>
                  </template>
                  <template v-else-if="column.key === 'generated_answer'">
                    <div v-if="record.generated_answer" class="benchmark-cell-secondary" :title="record.generated_answer">
                      {{ record.generated_answer }}
                    </div>
                    <span v-else class="benchmark-cell-empty">-</span>
                  </template>
                  <template v-else-if="column.key === 'retrieval_metrics'">
                    <div class="benchmark-metric-list">
                      <span v-for="metric in getRetrievalMetrics(record.metrics)" :key="metric.key">
                        <small>{{ metric.label }}</small>
                        <strong>{{ formatMetric(metric.value) }}</strong>
                      </span>
                      <span v-if="getRetrievalMetrics(record.metrics).length === 0" class="benchmark-cell-empty">-</span>
                    </div>
                  </template>
                  <template v-else-if="column.key === 'answer_score'">
                    <div v-if="record.metrics?.score !== undefined" class="benchmark-answer-score">
                      <span
                        class="benchmark-score-tag"
                        :class="record.metrics.score > 0.5 ? 'score-correct' : 'score-error'"
                      >
                        {{ record.metrics.score > 0.5 ? '正确' : '错误' }}
                      </span>
                      <span v-if="record.metrics.reasoning" :title="record.metrics.reasoning">
                        {{ record.metrics.reasoning }}
                      </span>
                    </div>
                    <span v-else class="benchmark-cell-empty">-</span>
                  </template>
                </template>
                <template #emptyText>
                  <a-empty
                    :description="results.length > 0 ? '当前页没有符合筛选条件的结果' : '暂无逐题结果'"
                  />
                </template>
              </a-table>
            </div>
          </template>
        </div>
      </template>
    </ExtensionDetailLayout>
  </div>
</template>

<script setup>
import { computed, h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BarChart3,
  ChevronRight,
  ClipboardList,
  Download,
  ListFilter,
  Search,
  WrapText
} from '@lucide/vue'
import ExtensionDetailLayout from '@/components/shared/ExtensionDetailLayout.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import { evaluationApi } from '@/apis/knowledge_api'
import { useDatabaseStore } from '@/stores/database'

const route = useRoute()
const router = useRouter()
const store = useDatabaseStore()
const kbId = computed(() => String(route.params.kbId || ''))
const datasetId = computed(() => String(route.params.datasetId || ''))
const database = computed(() => store.database)
const isCurrentDatabaseLoaded = computed(() => database.value?.kb_id === kbId.value)

const tabs = [
  { key: 'questions', label: '题目', icon: ClipboardList },
  { key: 'results', label: '评估结果', icon: BarChart3 }
]
const activeTab = ref(route.query.view === 'results' ? 'results' : 'questions')
const loading = ref(true)
const dataset = ref(null)
const questions = ref([])
const questionsLoading = ref(false)
const questionKeyword = ref('')
const questionAnnotationFilter = ref('all')
const questionAutoWrap = ref(false)
const questionPagination = reactive({ current: 1, pageSize: 50, total: 0 })

const runsLoading = ref(false)
const datasetRuns = ref([])
const selectedRunId = ref('')
const selectedRun = computed(() =>
  datasetRuns.value.find((run) => run.run_id === selectedRunId.value)
)
const results = ref([])
const resultsLoading = ref(false)
const resultKeyword = ref('')
const resultFilter = ref('all')
const resultAutoWrap = ref(false)
const resultPagination = reactive({ current: 1, pageSize: 50, total: 0 })
let refreshTimer = null
let resultRequestId = 0
let terminalRefreshAfterRequestId = 0
let terminalRefreshPending = false
let runsRefreshInFlight = false
let disposed = false

const annotationFilterOptions = [
  { value: 'all', label: '全部标注' },
  { value: 'chunks', label: '有 Gold Chunks' },
  { value: 'answer', label: '有 Gold Answer' },
  { value: 'query', label: '仅查询' }
]

const resultFilterOptions = [
  { value: 'all', label: '全部结果' },
  { value: 'answer_errors', label: '仅查看错误' },
  { value: 'errors_or_low_recall', label: '错误及 R@10 < 1' }
]

const questionColumns = computed(() => [
  { title: '#', key: 'index', width: 64, align: 'center' },
  { title: '问题', key: 'query', dataIndex: 'query', width: 360 },
  ...(dataset.value?.has_gold_chunks
    ? [{ title: 'Gold Chunks', key: 'gold_chunk_ids', width: 280 }]
    : []),
  ...(dataset.value?.has_gold_answers
    ? [{ title: 'Gold Answer', key: 'gold_answer', width: 420 }]
    : [])
])

const resultColumns = computed(() => [
  { title: '问题', key: 'query', dataIndex: 'query', width: 330 },
  { title: '生成答案', key: 'generated_answer', width: 390 },
  { title: '检索指标', key: 'retrieval_metrics', width: 360 },
  { title: '答案评判', key: 'answer_score', width: 310 }
])

const filteredQuestions = computed(() => {
  const keyword = questionKeyword.value.trim().toLowerCase()
  return questions.value.filter((question) => {
    const matchesKeyword =
      !keyword ||
      [question.query, question.gold_answer, ...(question.gold_chunk_ids || [])]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword))
    const filter = questionAnnotationFilter.value
    const matchesAnnotation =
      filter === 'all' ||
      (filter === 'chunks' && question.gold_chunk_ids?.length) ||
      (filter === 'answer' && question.gold_answer) ||
      (filter === 'query' && !question.gold_chunk_ids?.length && !question.gold_answer)
    return matchesKeyword && matchesAnnotation
  })
})

const filteredResults = computed(() => {
  const keyword = resultKeyword.value.trim().toLowerCase()
  if (!keyword) return results.value
  return results.value.filter((result) =>
    [result.query, result.generated_answer, result.gold_answer, result.metrics?.reasoning]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  )
})

const questionPaginationConfig = computed(() => ({
  current: questionPagination.current,
  pageSize: questionPagination.pageSize,
  total: questionPagination.total,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100'],
  showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
  onChange: (page, pageSize) => loadQuestions(page, pageSize)
}))

const resultPaginationConfig = computed(() => ({
  current: resultPagination.current,
  pageSize: resultPagination.pageSize,
  total: resultPagination.total,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100'],
  showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
  onChange: (page, pageSize) => loadResults(page, pageSize)
}))

const runOptions = computed(() =>
  datasetRuns.value.map((run) => ({
    value: run.run_id,
    label: getRunName(run)
  }))
)

const getRunName = (run) => run?.name || run?.run_name || '未命名测试'
const getRunStatusText = (status) =>
  ({ running: '运行中', completed: '已完成', failed: '失败', paused: '已暂停' })[status] || '未知状态'
const formatMetric = (value) => (Number.isFinite(value) ? Number(value).toFixed(2) : '-')
const formatScore = (value) => (Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '-')
const formatTime = (value) => (value ? new Date(value).toLocaleString('zh-CN') : '-')
const formatRunItems = (run) => `${run?.completed_items || 0}/${run?.total_items || 0}`

const formatRunDuration = (run) => {
  if (!run) return '-'
  if (run.status === 'running') return '进行中'
  if (!run.started_at || !run.completed_at) return '-'
  const seconds = Math.max(0, (new Date(run.completed_at) - new Date(run.started_at)) / 1000)
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
  return `${Math.floor(seconds / 3600)} 小时 ${Math.round((seconds % 3600) / 60)} 分`
}

const getResultRowKey = (record) => `${selectedRunId.value}:${record.item_index}`

const getRetrievalMetricLabel = (key) => {
  if (key.startsWith('recall')) return key.replace('recall', 'R').toUpperCase()
  if (key.startsWith('precision')) return key.replace('precision', 'P').toUpperCase()
  return key.toUpperCase()
}

const getRetrievalMetrics = (metrics = {}) =>
  Object.entries(metrics)
    .filter(([key, value]) =>
      Number.isFinite(value) &&
      (key.startsWith('recall') || key.startsWith('precision') || ['map', 'ndcg'].includes(key))
    )
    .map(([key, value]) => ({ key, label: getRetrievalMetricLabel(key), value }))

const loadQuestions = async (page = 1, pageSize = questionPagination.pageSize) => {
  questionsLoading.value = true
  try {
    const response = await evaluationApi.getDataset(kbId.value, datasetId.value, page, pageSize)
    if (response?.message !== 'success' || !response.data) throw new Error('评估基准数据格式错误')
    dataset.value = response.data
    questions.value = response.data.items || []
    questionPagination.current = response.data.pagination?.current_page || page
    questionPagination.pageSize = response.data.pagination?.page_size || pageSize
    questionPagination.total = response.data.pagination?.total_items || 0
  } catch (error) {
    console.error('加载评估基准题目失败:', error)
    message.error(error.message || '加载评估基准题目失败')
    dataset.value = null
  } finally {
    questionsLoading.value = false
  }
}

const stopRefreshTimer = () => {
  if (refreshTimer) window.clearTimeout(refreshTimer)
  refreshTimer = null
}

const syncRefreshTimer = () => {
  const shouldRefresh =
    selectedRun.value?.status === 'running' ||
    (activeTab.value === 'results' && terminalRefreshPending)
  if (disposed || !shouldRefresh) {
    stopRefreshTimer()
    return
  }
  if (runsRefreshInFlight) return
  if (!refreshTimer) {
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null
      loadRuns(true)
    }, 3000)
  }
}

const loadRuns = async (silent = false) => {
  if (runsRefreshInFlight) return
  runsRefreshInFlight = true
  if (!silent) runsLoading.value = true
  try {
    const response = await evaluationApi.listRuns(kbId.value)
    if (disposed) return
    if (response?.message !== 'success' || !Array.isArray(response.data)) {
      throw new Error('评估记录数据格式错误')
    }
    const selectedRunIdBeforeRefresh = selectedRunId.value
    const selectedRunWasRunning = selectedRun.value?.status === 'running'
    datasetRuns.value = response.data.filter((run) => run.dataset_id === datasetId.value)
    const requestedRunId = silent ? selectedRunId.value : String(route.query.run || '')
    const matchedRunId = datasetRuns.value.find((run) => run.run_id === requestedRunId)?.run_id
    if (matchedRunId) selectedRunId.value = matchedRunId
    else if (!silent) selectedRunId.value = datasetRuns.value[0]?.run_id || ''
    else {
      selectedRunId.value = ''
      resultRequestId += 1
      results.value = []
      resultsLoading.value = false
      resultPagination.total = 0
      terminalRefreshPending = false
    }
    if (
      silent &&
      selectedRunWasRunning &&
      selectedRunIdBeforeRefresh === selectedRunId.value &&
      selectedRun.value &&
      selectedRun.value.status !== 'running'
    ) {
      terminalRefreshPending = true
      terminalRefreshAfterRequestId = resultRequestId
    }
    if (activeTab.value === 'results' && selectedRunId.value) {
      const page = silent ? resultPagination.current : 1
      await loadResults(page, resultPagination.pageSize, silent)
    }
  } catch (error) {
    if (disposed) return
    console.error('加载基准评估记录失败:', error)
    if (!silent) message.error(error.message || '加载基准评估记录失败')
  } finally {
    runsRefreshInFlight = false
    if (!silent) runsLoading.value = false
    syncRefreshTimer()
  }
}

const loadResults = async (page = 1, pageSize = resultPagination.pageSize, silent = false) => {
  if (!selectedRunId.value) {
    results.value = []
    return
  }
  if (silent && resultsLoading.value) return
  if (!silent) {
    resultPagination.current = page
    resultPagination.pageSize = pageSize
  }
  const requestId = ++resultRequestId
  const runId = selectedRunId.value
  const filter = resultFilter.value
  if (!silent) resultsLoading.value = true
  try {
    const response = await evaluationApi.getRunResults(kbId.value, runId, {
      page,
      pageSize,
      resultFilter: filter
    })
    if (disposed || requestId !== resultRequestId) return
    if (response?.message !== 'success' || !response.data) throw new Error('评估结果数据格式错误')
    results.value = response.data.items || []
    resultPagination.current = response.data.pagination?.current_page || page
    resultPagination.pageSize = response.data.pagination?.page_size || pageSize
    resultPagination.total = response.data.pagination?.total || 0
    if (
      terminalRefreshPending &&
      requestId > terminalRefreshAfterRequestId &&
      runId === selectedRunId.value
    ) {
      terminalRefreshPending = false
      syncRefreshTimer()
    }
  } catch (error) {
    if (disposed || requestId !== resultRequestId) return
    console.error('加载逐题评估结果失败:', error)
    if (!silent) {
      message.error(error.message || '加载逐题评估结果失败')
      results.value = []
    }
  } finally {
    if (!silent && requestId === resultRequestId) resultsLoading.value = false
  }
}

const handleRunSelection = async (runId) => {
  selectedRunId.value = runId
  resultRequestId += 1
  terminalRefreshPending = false
  results.value = []
  resultPagination.current = 1
  syncRefreshTimer()
  await Promise.all([
    router.replace({ query: { ...route.query, view: 'results', run: runId } }),
    loadResults()
  ])
}

const handleResultFilterChange = () => {
  resultPagination.current = 1
  loadResults()
}

const downloadDataset = async () => {
  if (!dataset.value) return
  try {
    const response = await evaluationApi.downloadDataset(datasetId.value)
    const blob = await response.blob()
    const filename = `${dataset.value.name || datasetId.value}.jsonl`
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.error('下载评估基准失败:', error)
    message.error(error.message || '下载评估基准失败')
  }
}

const backToKnowledgeList = () => router.push({ path: '/extensions', query: { tab: 'knowledge' } })
const backToKnowledgeEvaluation = () =>
  router.push({
    name: 'ExtensionKnowledgeBaseDetail',
    params: { kbId: kbId.value },
    query: { section: 'evaluation' }
  })

const handleActiveTabChange = async (tab) => {
  if (!tabs.some((item) => item.key === tab)) return
  activeTab.value = tab
  syncRefreshTimer()

  const query = { ...route.query, view: tab }
  if (tab !== 'results') {
    delete query.run
  } else if (selectedRunId.value) {
    query.run = selectedRunId.value
  }
  await router.replace({ query })
  if (
    tab === 'results' &&
    selectedRunId.value &&
    (results.value.length === 0 || terminalRefreshPending)
  ) {
    await loadResults()
  }
}

watch(
  () => route.query.view,
  (view) => {
    activeTab.value = view === 'results' ? 'results' : 'questions'
  }
)

onMounted(async () => {
  loading.value = true
  store.kbId = kbId.value
  await Promise.all([store.getDatabaseInfo(kbId.value, true), loadQuestions(), loadRuns()])
  loading.value = false
})
onUnmounted(() => {
  disposed = true
  resultRequestId += 1
  stopRefreshTimer()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.benchmark-detail-container,
.benchmark-detail-layout {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.benchmark-breadcrumb {
  overflow: hidden;
}

.benchmark-detail-actions {
  min-width: 0;
}

.benchmark-run-select {
  width: max-content;
  min-width: 150px;
  max-width: min(360px, 38vw);
  padding: 0;
  color: var(--gray-900);
  font-size: 14px;
}

:deep(.benchmark-run-select.ant-select:not(.ant-select-disabled) .ant-select-selector) {
  padding: 0 24px 0 0;
  border: 0;
  box-shadow: none;
  color: var(--gray-900);
  font-weight: 600;
}

:deep(.benchmark-run-select .ant-select-selection-item) {
  max-width: 100%;
  overflow: hidden;
  padding-inline-end: 0;
  color: var(--gray-900);
  font-weight: 600 !important;
  text-overflow: ellipsis;
}

:deep(.benchmark-run-select .ant-select-arrow) {
  right: 0;
}

.benchmark-detail-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 18px var(--page-padding) 16px;
}

.benchmark-summary-strip,
.benchmark-result-summary {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 0 0 14px;
  border-bottom: 1px solid var(--gray-150);
}

.benchmark-summary-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;

  strong {
    overflow: hidden;
    color: var(--gray-900);
    font-size: 15px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  span {
    overflow: hidden;
    color: var(--gray-500);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.benchmark-summary-metrics,
.benchmark-result-metrics {
  display: flex;
  align-items: center;
  gap: 26px;

  > span {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 3px;
    white-space: nowrap;
  }

  small {
    color: var(--gray-500);
    font-size: 10px;
  }

  strong {
    color: var(--gray-900);
    font-family: var(--mono-font, ui-monospace, SFMono-Regular, Menlo, monospace);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
}

.benchmark-table-toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
}

.benchmark-filter-group {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.benchmark-search-input {
  width: 260px;
}

.benchmark-filter-select {
  width: 160px;
}

.benchmark-filter-note {
  color: var(--gray-400);
  font-size: 11px;
  white-space: nowrap;
}

.benchmark-toolbar-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.benchmark-wrap-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  padding: 0;
}

.benchmark-table-area {
  flex: 1;
  min-height: 0;
  overflow: auto;

  :deep(.ant-table-wrapper) {
    width: 100%;
    min-width: 0;
    overflow: hidden;
  }

  :deep(.ant-table-container),
  :deep(.ant-table-content),
  :deep(.ant-table-body) {
    max-width: 100%;
  }

  :deep(.ant-table-thead > tr > th) {
    padding: 8px 10px;
    background: var(--gray-50);
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 600;
  }

  :deep(.ant-table-tbody > tr > td) {
    padding: 8px 10px;
    color: var(--gray-700);
    font-size: 12px;
    line-height: 1.5;
    vertical-align: top;
  }

  :deep(.ant-table-tbody > tr:hover > td) {
    background: var(--gray-25);
  }

  :deep(.ant-table-pagination) {
    margin: 12px 0 0;
  }
}

.benchmark-index {
  color: var(--gray-500);
  font-family: var(--mono-font, ui-monospace, monospace);
  font-variant-numeric: tabular-nums;
}

.benchmark-cell-primary,
.benchmark-cell-secondary,
.benchmark-cell-code {
  white-space: normal;
  word-break: break-word;
}

.benchmark-cell-primary {
  color: var(--gray-900);
  font-size: 13px;
}

.benchmark-cell-secondary {
  color: var(--gray-600);
}

.benchmark-cell-code {
  color: var(--gray-600);
  font-family: var(--mono-font, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 11px;
}

.benchmark-cell-empty {
  color: var(--gray-400);
}

:deep(.benchmark-table-nowrap .ant-table-cell) {
  overflow: hidden;
  white-space: nowrap;
}

:deep(.benchmark-table-nowrap .ant-table) {
  width: 100% !important;
}

:deep(.benchmark-table-nowrap) {
  .benchmark-cell-primary,
  .benchmark-cell-secondary,
  .benchmark-cell-code,
  .benchmark-answer-score > span:last-child {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .benchmark-metric-list {
    flex-wrap: nowrap;
  }
}

.benchmark-result-empty {
  flex: 1;
}

.benchmark-run-identity {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.benchmark-run-meta {
  min-width: 0;
  color: var(--gray-500);
  font-size: 11px;
  white-space: nowrap;
}

.benchmark-run-status,
.benchmark-score-tag {
  flex: 0 0 auto;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  font-weight: 500;

  &.status-running {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }

  &.status-completed,
  &.score-correct {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.status-failed,
  &.score-error {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}

.benchmark-metric-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;

  > span:not(.benchmark-cell-empty) {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
  }

  small {
    color: var(--gray-500);
    font-size: 10px;
  }

  strong {
    color: var(--gray-800);
    font-family: var(--mono-font, ui-monospace, monospace);
    font-size: 11px;
  }
}

.benchmark-answer-score {
  display: flex;
  align-items: flex-start;
  gap: 8px;

  > span:last-child {
    min-width: 0;
    color: var(--gray-600);
  }
}

@media (max-width: 900px) {
  .benchmark-run-select {
    max-width: min(300px, 40vw);
  }

  .benchmark-summary-metrics,
  .benchmark-result-metrics {
    gap: 14px;
  }

  .benchmark-filter-note {
    display: none;
  }
}

@media (max-width: 680px) {
  .benchmark-detail-panel {
    padding: 14px 16px;
  }

  .benchmark-run-select {
    width: max-content;
    min-width: 0;
    max-width: min(260px, 70vw);
  }

  .benchmark-run-identity {
    width: 100%;
    flex-wrap: wrap;
  }

  .benchmark-run-meta {
    flex: 1 1 auto;
  }

  .benchmark-summary-strip,
  .benchmark-result-summary {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }

  .benchmark-summary-metrics,
  .benchmark-result-metrics {
    width: 100%;
    justify-content: space-between;

    > span {
      align-items: flex-start;
    }
  }

  .benchmark-table-toolbar {
    align-items: stretch;
    flex-direction: column;
    gap: 10px;
  }

  .benchmark-filter-group {
    flex-wrap: wrap;
  }

  .benchmark-search-input {
    width: 100%;
  }

  .benchmark-wrap-button {
    align-self: flex-end;
  }
}
</style>

<style lang="less">
@media (max-width: 767px) {
  .app-layout:has(.benchmark-detail-container) {
    min-width: 0;
  }
}
</style>
