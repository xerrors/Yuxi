<template>
  <div class="rag-evaluation-container">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <!-- 基准选择 -->
        <div class="benchmark-selector">
          <label class="selector-label">评估基准</label>
          <a-select
            v-model:value="selectedBenchmarkId"
            placeholder="请选择评估基准"
            style="width: 240px"
            @change="onBenchmarkChanged"
            :loading="benchmarksLoading"
          >
            <a-select-option
              v-for="benchmark in availableBenchmarks"
              :key="benchmark.benchmark_id"
              :value="benchmark.benchmark_id"
            >
              {{ benchmark.name }} ({{ benchmark.question_count }} 个问题)
            </a-select-option>
          </a-select>
          <a-button
            type="text"
            size="middle"
            :loading="benchmarksLoading"
            @click="() => loadBenchmarks(true)"
            :icon="h(RefreshCw, { size: 16 })"
            class="refresh-benchmarks-btn lucide-icon-btn"
            title="刷新评估基准列表"
          />
        </div>
      </div>
      <div class="toolbar-right">
        <!-- 开始评估按钮 -->
        <a-button
          type="primary"
          :loading="startingEvaluation"
          @click="startEvaluation"
          :disabled="!selectedBenchmark"
          size="middle"
        >
          开始评估
        </a-button>
      </div>
    </div>

    <!-- 评估结果区域 -->
    <div class="evaluation-results">
      <!-- 模型配置（仅在选中基准且需要时显示） -->
      <div
        v-if="
          selectedBenchmark &&
          (selectedBenchmark.has_gold_chunks || selectedBenchmark.has_gold_answers)
        "
        class="model-config-section"
      >
        <a-row :gutter="24">
          <a-col :span="12">
            <a-form-item
              :label="
                selectedBenchmark.has_gold_answers
                  ? '答案生成模型（可选）'
                  : '答案生成模型（当前基准无需）'
              "
            >
              <ModelSelectorComponent
                v-model:model_spec="configForm.answer_llm"
                size="small"
                :disabled="!selectedBenchmark || !selectedBenchmark.has_gold_answers"
                @select-model="(value) => (configForm.answer_llm = value)"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>

          <a-col :span="12">
            <a-form-item
              :label="
                selectedBenchmark.has_gold_answers
                  ? '答案评判模型（可选）'
                  : '答案评判模型（当前基准无需）'
              "
            >
              <ModelSelectorComponent
                v-model:model_spec="configForm.judge_llm"
                size="small"
                :disabled="!selectedBenchmark || !selectedBenchmark.has_gold_answers"
                @select-model="(value) => (configForm.judge_llm = value)"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </div>

      <template v-if="!selectedBenchmark">
        <div class="empty-state">
          <a-empty description="请在顶部选择评估基准或前往基准管理">
            <a-space>
              <a-button @click="$emit('switch-to-benchmarks')"> 前往基准管理 </a-button>
            </a-space>
          </a-empty>
        </div>
      </template>
      <template v-else>
        <!-- 历史评估记录 -->
        <div class="history-section">
          <div class="section-header">
            <h4 class="section-title">历史评估记录</h4>
            <a-button
              type="text"
              size="small"
              :loading="refreshingHistory"
              @click="refreshHistory"
              :icon="h(RefreshCw, { size: 14 })"
              class="refresh-btn lucide-icon-btn"
            >
              刷新
            </a-button>
          </div>
          <a-table
            class="history-table"
            :columns="historyColumns"
            :data-source="evaluationHistory"
            :pagination="{ pageSize: 10 }"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'overall_score'">
                <span v-if="record.overall_score !== null">
                  <a-tag :color="getScoreTagColor(record.overall_score)">
                    {{ (record.overall_score * 100).toFixed(0) }}%
                  </a-tag>
                </span>
                <span v-else>-</span>
              </template>
              <template v-else-if="column.key === 'actions'">
                <a-space>
                  <a-button
                    v-if="record.status === 'completed'"
                    type="link"
                    size="small"
                    @click="viewResults(record.task_id)"
                  >
                    查看
                  </a-button>
                  <a-tag v-else :color="getStatusColor(record.status)">
                    {{ getStatusText(record.status) }}
                  </a-tag>
                  <a-popconfirm
                    title="确定要删除这条评估记录吗？"
                    description="删除后将无法恢复"
                    @confirm="deleteEvaluationRecord(record.task_id)"
                    ok-text="确定"
                    cancel-text="取消"
                  >
                    <a-button type="link" size="small" danger :loading="record.deleting">
                      删除
                    </a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </template>
          </a-table>
        </div>
      </template>
    </div>
  </div>

  <!-- 评估结果详情模态框 -->
  <a-modal
    v-model:open="resultModalVisible"
    :title="`评估结果 - ${selectedResult?.task_id?.slice(0, 8) || ''}`"
    width="1200px"
    :footer="null"
  >
    <div v-if="resultsLoading" class="loading-container">
      <a-spin size="large" />
      <p style="margin-top: 16px; color: var(--gray-600)">正在加载评估结果...</p>
    </div>

    <div v-else-if="selectedResult && detailedResults.length > 0">
      <!-- 基本信息 -->
      <a-descriptions
        title="基本信息"
        :column="3"
        size="small"
        bordered
        style="margin-bottom: 20px"
      >
        <a-descriptions-item label="任务ID">{{ selectedResult.task_id }}</a-descriptions-item>
        <a-descriptions-item label="状态">
          <a-tag :color="getStatusColor(selectedResult.status)">
            {{ getStatusText(selectedResult.status) }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="总体评分">
          <span v-if="selectedResult.overall_score !== null">
            <a-tag :color="getScoreTagColor(selectedResult.overall_score)">
              {{ (selectedResult.overall_score * 100).toFixed(1) }}%
            </a-tag>
          </span>
          <span v-else>-</span>
        </a-descriptions-item>
        <a-descriptions-item label="总问题数">{{
          selectedResult.total_questions
        }}</a-descriptions-item>
        <a-descriptions-item label="完成数">{{
          selectedResult.completed_questions
        }}</a-descriptions-item>
        <a-descriptions-item label="总耗时">
          <span v-if="evaluationStats.totalDuration">
            {{ formatDuration(evaluationStats.totalDuration) }}
          </span>
          <span v-else>-</span>
        </a-descriptions-item>
      </a-descriptions>

      <!-- 检索配置和整体评估报告 -->
      <a-row :gutter="16" style="margin-bottom: 20px">
        <!-- 检索配置 -->
        <a-col :span="12" v-if="selectedResult.retrieval_config">
          <a-card size="small" title="检索配置">
            <div class="json-viewer-container">
              <pre class="json-viewer">{{
                JSON.stringify(selectedResult.retrieval_config, null, 2)
              }}</pre>
            </div>
          </a-card>
        </a-col>

        <!-- 整体评估报告 -->
        <a-col :span="selectedResult.retrieval_config ? 12 : 24">
          <a-card size="small" title="整体评估报告">
            <!-- 检索指标 -->
            <div style="margin-bottom: 20px">
              <h5 style="margin-bottom: 12px; font-size: 14px; font-weight: 500">检索指标</h5>
              <div v-if="Object.keys(evaluationStats.retrievalMetrics || {}).length > 0">
                <div
                  v-for="(value, key) in evaluationStats.retrievalMetrics"
                  :key="key"
                  class="report-metric"
                >
                  <span class="metric-label">{{ getMetricTitle(key) }}：</span>
                  <span class="metric-value" :style="{ color: getScoreColor(value) }">
                    {{ formatMetricValue(value) }}
                  </span>
                </div>
              </div>
              <span v-else class="no-metrics">-</span>
            </div>

            <!-- 答案准确性 -->
            <div>
              <h5 style="margin-bottom: 12px; font-size: 14px; font-weight: 500">答案准确性</h5>
              <div class="accuracy-stats">
                <div class="accuracy-item">
                  <span class="accuracy-label">正确答案数：</span>
                  <span class="accuracy-value"
                    >{{ evaluationStats.correctAnswers || 0 }} /
                    {{ evaluationStats.totalQuestions || 0 }}</span
                  >
                </div>
                <div class="accuracy-item">
                  <span class="accuracy-label">准确率：</span>
                  <span
                    class="accuracy-value"
                    :style="{ color: getScoreColor(evaluationStats.answerAccuracy) }"
                  >
                    {{ (evaluationStats.answerAccuracy * 100).toFixed(1) }}%
                  </span>
                </div>
              </div>
            </div>
          </a-card>
        </a-col>
      </a-row>

      <!-- 详细结果表格 -->
      <div
        style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
        "
      >
        <div>
          <h4 style="margin: 0">详细评估结果</h4>
          <span style="font-size: 12px; color: var(--gray-600); margin-left: 8px">
            {{
              showErrorsOnly
                ? `仅显示错误结果（共 ${paginationTotal} 条）`
                : `显示全部结果（共 ${paginationTotal} 条）`
            }}
          </span>
        </div>
        <a-button
          type="default"
          size="small"
          @click="toggleErrorOnly"
          :class="{ 'error-only-active': showErrorsOnly }"
        >
          {{ showErrorsOnly ? '显示全部' : '仅查看错误' }}
        </a-button>
      </div>
      <a-table
        :columns="resultColumns"
        :data-source="detailedResults"
        :pagination="{
          current: currentPage,
          pageSize: pageSize,
          total: paginationTotal,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
          onChange: handlePageChange,
          onShowSizeChange: handlePageSizeChange
        }"
        :scroll="{ x: 1000 }"
        size="small"
        :loading="resultsLoading"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'query'">
            <a-tooltip :title="record.query">
              <div class="query-text">{{ record.query }}</div>
            </a-tooltip>
          </template>
          <template v-else-if="column.key === 'generated_answer'">
            <a-tooltip :title="record.generated_answer">
              <div class="answer-text">{{ record.generated_answer || '-' }}</div>
            </a-tooltip>
          </template>
          <template v-else-if="column.key === 'retrieval_score'">
            <div
              v-if="
                record.metrics &&
                Object.keys(record.metrics).some(
                  (k) =>
                    k.startsWith('recall') ||
                    k.startsWith('precision') ||
                    k === 'map' ||
                    k === 'ndcg'
                )
              "
              class="retrieval-metrics"
            >
              <div v-for="(val, key) in record.metrics" :key="key" class="metric-item">
                <span
                  v-if="
                    key.startsWith('recall') ||
                    key.startsWith('precision') ||
                    key === 'map' ||
                    key === 'ndcg'
                  "
                  class="metric-content"
                  :class="`metric-${getMetricType(key)}`"
                >
                  <span class="metric-name">{{ getMetricShortName(key) }}</span>
                  <span class="metric-value">{{ formatMetricValue(val) }}</span>
                </span>
              </div>
            </div>
            <span v-else class="no-metrics">-</span>
          </template>
          <template v-else-if="column.key === 'answer_score'">
            <div v-if="record.metrics && record.metrics.score !== undefined">
              <a-tag :color="record.metrics.score > 0.5 ? 'green' : 'red'">
                {{ record.metrics.score === 1.0 ? '正确' : '错误' }}
              </a-tag>
              <div v-if="record.metrics.reasoning" class="answer-reasoning">
                <a-tooltip :title="record.metrics.reasoning">
                  {{ record.metrics.reasoning }}
                </a-tooltip>
              </div>
            </div>
            <span v-else>-</span>
          </template>
        </template>
      </a-table>
    </div>

    <div v-else-if="selectedResult" class="empty-results">
      <a-empty description="暂无详细结果数据">
        <a-button @click="viewDetails(selectedResult)">查看基本信息</a-button>
      </a-empty>
    </div>
  </a-modal>
</template>

<script setup>
import { ref, reactive, onMounted, computed, h } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { evaluationApi } from '@/apis/knowledge_api'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import { RefreshCw } from 'lucide-vue-next'
import { useTaskerStore } from '@/stores/tasker'

const props = defineProps({
  databaseId: {
    type: String,
    required: true
  }
})

defineEmits(['switch-to-benchmarks'])

// 使用任务中心 store
const taskerStore = useTaskerStore()

// 状态
const selectedBenchmarkId = ref(null)
const selectedBenchmark = ref(null)
const benchmarksLoading = ref(false)
const availableBenchmarks = ref([])
const startingEvaluation = ref(false)
const evaluationHistory = ref([])
const resultModalVisible = ref(false)
const selectedResult = ref(null)
const detailedResults = ref([])
const evaluationStats = ref({})
const resultsLoading = ref(false)
const refreshingHistory = ref(false)
const showErrorsOnly = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const paginationTotal = ref(0)
const paginationTotalPages = ref(0)

// 评估配置表单（使用知识库默认配置）
const configForm = reactive({
  answer_llm: '', // 答案生成模型
  judge_llm: '' // 评判模型
})

// 表格列定义
const resultColumns = computed(() => {
  const columns = [
    {
      title: '问题',
      dataIndex: 'query',
      key: 'query',
      width: 100
    },
    {
      title: '生成答案',
      key: 'generated_answer',
      width: 180
    },
    {
      title: '答案评判',
      key: 'answer_score',
      width: 260
    }
  ]

  // 检查是否有检索指标数据
  const hasRetrievalMetrics = detailedResults.value.some((item) => {
    if (!item.metrics) return false
    return Object.keys(item.metrics).some(
      (key) =>
        key.startsWith('recall') || key.startsWith('precision') || key === 'map' || key === 'ndcg'
    )
  })

  // 如果有检索指标数据，添加检索指标列
  if (hasRetrievalMetrics) {
    columns.splice(2, 0, {
      title: '检索指标',
      key: 'retrieval_score',
      width: 100
    })
  }

  return columns
})

const historyColumns = [
  {
    title: '开始时间',
    dataIndex: 'started_at',
    key: 'started_at',
    width: 180,
    customRender: ({ record }) => formatTime(record.started_at)
  },
  {
    title: '评估基准',
    key: 'benchmark_name',
    width: 200,
    customRender: ({ record }) => {
      // 根据 benchmark_id 查找基准名称
      const benchmark = availableBenchmarks.value.find(
        (b) => b.benchmark_id === record.benchmark_id
      )
      return benchmark ? benchmark.name : record.benchmark_id?.slice(0, 8) || '-'
    }
  },
  {
    title: 'Recall@10',
    key: 'recall_10',
    width: 100,
    customRender: ({ record }) => {
      // 使用后端返回的 metrics.recall@10 数据
      if (
        record.metrics &&
        record.metrics['recall@10'] !== undefined &&
        record.metrics['recall@10'] !== null
      ) {
        const recallValue = record.metrics['recall@10']
        const displayValue = formatMetricValue(recallValue)
        return h(
          'a-tag',
          {
            color: getScoreTagColor(recallValue)
          },
          displayValue
        )
      }

      // 如果是运行中的任务，显示计算中
      if (record.status === 'running') {
        return h(
          'a-tag',
          {
            color: 'processing'
          },
          '计算中'
        )
      }

      // 已完成但没有 recall@10 数据
      if (record.status === 'completed') {
        return h(
          'a-tag',
          {
            color: 'default'
          },
          '无数据'
        )
      }

      // 其他情况显示横线
      return h('span', '-')
    }
  },
  {
    title: '操作',
    key: 'actions',
    width: 100
  }
]

// 切换错误显示模式
const toggleErrorOnly = async () => {
  resultsLoading.value = true
  showErrorsOnly.value = !showErrorsOnly.value
  currentPage.value = 1 // 切换模式时重置到第一页

  // 立即加载新的分页数据
  await loadResultsWithPagination()
}

// 处理分页变化
const handlePageChange = (page, size) => {
  currentPage.value = page
  if (size !== pageSize.value) {
    pageSize.value = size
  }
  loadResultsWithPagination()
}

// 处理页面大小变化
const handlePageSizeChange = (current, size) => {
  currentPage.value = 1
  pageSize.value = size
  loadResultsWithPagination()
}

// 加载分页结果
const loadResultsWithPagination = async () => {
  if (!selectedResult.value) return

  try {
    resultsLoading.value = true
    const response = await evaluationApi.getEvaluationResultsByDb(
      props.databaseId,
      selectedResult.value.task_id,
      {
        page: currentPage.value,
        pageSize: pageSize.value,
        errorOnly: showErrorsOnly.value
      }
    )

    if (response.message === 'success' && response.data) {
      const resultData = response.data

      // 更新详细结果
      detailedResults.value = resultData.interim_results || []

      // 更新分页信息
      if (resultData.pagination) {
        paginationTotal.value = resultData.pagination.total
        paginationTotalPages.value = resultData.pagination.total_pages
      } else {
        // 兼容旧格式数据
        paginationTotal.value = detailedResults.value.length
        paginationTotalPages.value = 1
      }

      // 更新统计信息
      // 如果是过滤模式，需要基于过滤后的总数计算统计
      if (showErrorsOnly.value) {
        // 在过滤模式下，只计算当前页的统计（避免重复计算）
        evaluationStats.value = {
          ...evaluationStats.value,
          totalQuestions: paginationTotal.value
          // 可以在这里添加其他基于过滤后数据的统计
        }
      } else if (currentPage.value === 1) {
        // 非过滤模式且是第一页时，才计算完整统计
        evaluationStats.value = calculateEvaluationStats(detailedResults.value)
      }

      // 更新其他基本信息（保持原有的信息不变）
      if (resultData.started_at && resultData.completed_at) {
        const startTime = new Date(resultData.started_at)
        const endTime = new Date(resultData.completed_at)
        evaluationStats.value.totalDuration = (endTime - startTime) / 1000
      }
    }
  } catch (error) {
    console.error('加载评估结果失败:', error)
    message.error('加载评估结果失败')
  } finally {
    resultsLoading.value = false
  }
}

// 加载基准列表
const loadBenchmarks = async (showSuccessMessage = false) => {
  if (!props.databaseId) return

  benchmarksLoading.value = true
  try {
    const response = await evaluationApi.getBenchmarks(props.databaseId)

    if (response && response.message === 'success' && Array.isArray(response.data)) {
      availableBenchmarks.value = response.data

      // 如果没有选中的基准，且有可用基准，默认选中第一个
      if (!selectedBenchmarkId.value && response.data.length > 0) {
        selectedBenchmarkId.value = response.data[0].benchmark_id
        selectedBenchmark.value = response.data[0]
      } else if (selectedBenchmarkId.value) {
        // 如果之前有选中的基准，重新验证其有效性
        const exists = response.data.some((b) => b.benchmark_id === selectedBenchmarkId.value)
        if (!exists) {
          selectedBenchmarkId.value = null
          selectedBenchmark.value = null
        } else {
          // 更新选中的基准对象
          selectedBenchmark.value = response.data.find(
            (b) => b.benchmark_id === selectedBenchmarkId.value
          )
        }
      }

      // 如果是手动刷新，显示成功提示
      if (showSuccessMessage) {
        message.success(`已刷新，找到 ${response.data.length} 个评估基准`)
      }
    } else {
      console.error('响应格式不符合预期:', response)
      message.error('基准数据格式错误')
    }
  } catch (error) {
    console.error('加载评估基准失败:', error)
    message.error('加载评估基准失败')
  } finally {
    benchmarksLoading.value = false
  }
}

// 下拉框选择变化
const onBenchmarkChanged = (benchmarkId) => {
  const benchmark = availableBenchmarks.value.find((b) => b.benchmark_id === benchmarkId)
  selectedBenchmark.value = benchmark || null
}

// 刷新历史评估记录
const refreshHistory = async () => {
  refreshingHistory.value = true
  try {
    await loadEvaluationHistory()
    message.success('历史记录已刷新')
  } catch (error) {
    console.error('刷新历史记录失败:', error)
    message.error('刷新历史记录失败')
  } finally {
    refreshingHistory.value = false
  }
}

// 开始评估
const startEvaluation = async () => {
  if (!selectedBenchmark.value) {
    message.error('请先选择评估基准')
    return
  }

  // 校验模型选择：必须同时选择或同时不选择
  const hasAnswerModel = !!configForm.answer_llm
  const hasJudgeModel = !!configForm.judge_llm

  if (hasAnswerModel !== hasJudgeModel) {
    message.warning('生成模型和评估模型必须同时选择或者同时不选择')
    return
  }

  const runEvaluation = async () => {
    startingEvaluation.value = true

    // 只传递模型配置，检索配置由服务器从知识库读取
    const params = {
      benchmark_id: selectedBenchmark.value.benchmark_id,
      model_config: {
        answer_llm: configForm.answer_llm, // 传递答案生成模型
        judge_llm: configForm.judge_llm // 传递评判模型
      }
    }

    try {
      const response = await evaluationApi.runEvaluation(props.databaseId, params)

      if (response.message === 'success') {
        message.success('评估任务已开始')
        loadEvaluationHistory()
        // 刷新任务中心的任务列表
        taskerStore.loadTasks()
      } else {
        message.error(response.message || '启动评估失败')
      }
    } catch (error) {
      console.error('启动评估失败:', error)
      message.error('启动评估失败')
    } finally {
      startingEvaluation.value = false
    }
  }

  if (!hasAnswerModel) {
    Modal.confirm({
      title: '确认评估模式',
      content: '您未选择答案生成模型，将仅进行检索测试，不会进行问答测试。是否继续？',
      okText: '继续',
      cancelText: '取消',
      onOk: runEvaluation
    })
  } else {
    runEvaluation()
  }
}

// 加载评估历史
const loadEvaluationHistory = async () => {
  try {
    const response = await evaluationApi.getEvaluationHistory(props.databaseId)
    if (response.message === 'success') {
      evaluationHistory.value = response.data || []
    }
  } catch (error) {
    console.error('加载评估历史失败:', error)
    message.error('加载评估历史失败')
  }
}

// 计算评估统计信息
const calculateEvaluationStats = (results) => {
  if (!results || results.length === 0) {
    return {}
  }

  const stats = {
    totalQuestions: results.length,
    retrievalMetrics: {},
    answerAccuracy: 0,
    correctAnswers: 0,
    averageResponseTime: 0,
    totalResponseTime: 0
  }

  const metricSums = {}
  const metricCounts = {}

  results.forEach((item) => {
    // 答案准确率
    if (item.metrics && item.metrics.score !== undefined) {
      if (item.metrics.score > 0.5) {
        stats.correctAnswers++
      }
    }

    // 检索指标统计
    if (item.metrics) {
      Object.keys(item.metrics).forEach((key) => {
        if (
          key.startsWith('recall') ||
          key.startsWith('precision') ||
          key === 'map' ||
          key === 'ndcg'
        ) {
          if (!metricSums[key]) {
            metricSums[key] = 0
            metricCounts[key] = 0
          }
          metricSums[key] += item.metrics[key]
          metricCounts[key]++
        }
      })
    }
  })

  // 计算平均值
  Object.keys(metricSums).forEach((key) => {
    stats.retrievalMetrics[key] = metricSums[key] / metricCounts[key]
  })

  // 计算答案准确率
  stats.answerAccuracy = stats.totalQuestions > 0 ? stats.correctAnswers / stats.totalQuestions : 0

  return stats
}

// 查看结果
const viewResults = async (taskId) => {
  try {
    resultsLoading.value = true

    // 重置分页状态
    currentPage.value = 1
    showErrorsOnly.value = false

    // 先获取基本信息（不分页）
    const response = await evaluationApi.getEvaluationResultsByDb(props.databaseId, taskId)

    if (response.message === 'success' && response.data) {
      const resultData = response.data

      // 从历史记录中找到对应的任务信息，如果没有则使用API返回的数据
      selectedResult.value = evaluationHistory.value.find((r) => r.task_id === taskId) || {
        task_id: resultData.task_id,
        status: resultData.status,
        started_at: resultData.started_at,
        completed_at: resultData.completed_at,
        total_questions: resultData.total_questions || 0,
        completed_questions: resultData.completed_questions || 0,
        overall_score: resultData.overall_score,
        retrieval_config: resultData.retrieval_config
      }

      // 如果是从历史记录获取的，确保也有 retrieval_config
      if (selectedResult.value && !selectedResult.value.retrieval_config) {
        selectedResult.value.retrieval_config = resultData.retrieval_config
      }

      // 打开模态框
      resultModalVisible.value = true

      // 加载分页数据
      await loadResultsWithPagination()
    } else {
      message.error('获取评估结果失败：数据格式错误')
    }
  } catch (error) {
    console.error('获取评估结果失败:', error)
    message.error('获取评估结果失败')
  } finally {
    resultsLoading.value = false
  }
}

// 删除评估记录
const deleteEvaluationRecord = async (taskId) => {
  try {
    // 找到对应的记录并设置loading状态
    const record = evaluationHistory.value.find((r) => r.task_id === taskId)
    if (record) {
      record.deleting = true
    }

    const response = await evaluationApi.deleteEvaluationResultByDb(props.databaseId, taskId)
    if (response.message === 'success') {
      message.success('删除成功')
      // 重新加载评估历史
      await loadEvaluationHistory()
    }
  } catch (error) {
    console.error('删除评估记录失败:', error)
    message.error('删除评估记录失败')
  } finally {
    // 清除loading状态
    const record = evaluationHistory.value.find((r) => r.task_id === taskId)
    if (record) {
      record.deleting = false
    }
  }
}

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN')
}

const getScoreColor = (score) => {
  if (score >= 0.8) return 'var(--color-success-500)'
  if (score >= 0.6) return 'var(--color-warning-500)'
  return 'var(--color-error-500)'
}

const getScoreTagColor = (score) => {
  if (score >= 0.8) return 'success'
  if (score >= 0.6) return 'warning'
  return 'error'
}

const getStatusColor = (status) => {
  const colors = {
    running: 'blue',
    completed: 'green',
    failed: 'red',
    paused: 'orange'
  }
  return colors[status] || 'default'
}

const getStatusText = (status) => {
  const texts = {
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    paused: '已暂停'
  }
  return texts[status] || status
}

const getMetricTitle = (key) => {
  const titles = {
    precision: '精确率',
    recall: '召回率',
    map: '平均精度',
    ndcg: 'NDCG',
    bleu: 'BLEU分数',
    rouge: 'ROUGE分数',
    answer_correctness: '答案准确性',
    score: '评分',
    reasoning: '理由',
    overall_score: '综合评分'
  }
  // 处理 recall@k
  if (key.startsWith('recall@')) return `召回率 (${key.split('@')[1]})`
  if (key.startsWith('precision@')) return `精确率 (${key.split('@')[1]})`

  return titles[key] || key
}

// 获取指标类型
const getMetricType = (key) => {
  if (key.startsWith('recall')) return 'recall'
  if (key.startsWith('precision')) return 'precision'
  if (key === 'map') return 'map'
  if (key === 'ndcg') return 'ndcg'
  return 'default'
}

// 获取指标短名称
const getMetricShortName = (key) => {
  if (key.startsWith('recall@')) return `R@${key.split('@')[1]}`
  if (key.startsWith('precision@')) return `P@${key.split('@')[1]}`
  if (key === 'precision') return 'Precision'
  if (key === 'recall') return 'Recall'
  if (key === 'map') return 'MAP'
  if (key === 'ndcg') return 'NDCG'
  return key
}

// 格式化指标值
const formatMetricValue = (val) => {
  if (typeof val !== 'number') return '-'
  // 检索指标（recall, precision, f1 等）范围是 0.0-1.0，统一转换为百分比
  if (val <= 1) return (val * 100).toFixed(1) + '%'
  return val.toFixed(3)
}

// 格式化持续时间
const formatDuration = (seconds) => {
  if (seconds < 60) {
    return `${Math.round(seconds)}秒`
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = Math.round(seconds % 60)
    return `${minutes}分${remainingSeconds}秒`
  } else {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    return `${hours}小时${minutes}分`
  }
}

// 组件挂载时加载数据
onMounted(() => {
  loadBenchmarks()
  loadEvaluationHistory()
})
</script>

<style lang="less" scoped>
.rag-evaluation-container {
  height: 100%;
  background: var(--gray-0);
  display: flex;
  flex-direction: column;
}

// 顶部工具栏
.toolbar {
  padding: 12px 16px;
  background: var(--gray-0);
  border-bottom: 1px solid var(--gray-200);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;

  .toolbar-left {
    display: flex;
    align-items: center;
  }

  .benchmark-selector {
    display: flex;
    align-items: center;
    gap: 12px;

    .selector-label {
      font-size: 14px;
      font-weight: 500;
      color: var(--gray-700);
      margin: 0;
      white-space: nowrap;
    }

    .refresh-benchmarks-btn {
      color: var(--gray-600);
    }
  }

  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 6px;
  }
}

// 评估内容区域
.evaluation-content {
  flex: 1;
  overflow: hidden;
  min-height: 0;
  padding: 10px 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

// 评估结果区域
.evaluation-results {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: auto;
  padding: 16px;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-track {
    background: transparent;
  }

  &::-webkit-scrollbar-thumb {
    background-color: rgba(0, 0, 0, 0.2);
    border-radius: 3px;

    &:hover {
      background-color: rgba(0, 0, 0, 0.3);
    }
  }
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gray-0);
  border-radius: 8px;
  border: 1px solid var(--gray-200);
}

.progress-info {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;

  .progress-stats {
    flex: 1;
    margin-right: 24px;
    min-width: 300px;

    .ant-statistic {
      margin-bottom: 12px;

      .ant-statistic-title {
        font-size: 13px;
        color: var(--gray-600);
      }

      .ant-statistic-content {
        font-size: 18px;
        font-weight: 500;
      }
    }
  }

  .progress-actions {
    flex-shrink: 0;
    padding-top: 24px;
  }
}

.query-text {
  font-size: 12px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  line-height: 1.5;
  word-wrap: break-word;
  overflow: hidden;
  text-overflow: ellipsis;
}

.answer-text {
  font-size: 12px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  line-height: 1.5;
  word-wrap: break-word;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--gray-700);
}

.log-time {
  color: var(--gray-500);
  margin-left: 8px;
  font-size: 12px;
}

// 优化表格样式
:deep(.ant-table) {
  .ant-table-tbody > tr > td {
    padding: 12px 12px;
    vertical-align: top;
  }

  .ant-table-thead > tr > th {
    padding: 8px 12px;
    font-weight: 500;
    background-color: var(--gray-50);
  }
}

// 优化卡片间距
:deep(.ant-card) {
  .ant-card-head {
    padding: 8px 16px;
    min-height: 40px;

    .ant-card-head-title {
      font-size: 14px;
      font-weight: 500;
      padding: 4px 0;
    }
  }
}

// 优化时间线样式
:deep(.ant-timeline) {
  .ant-timeline-item-content {
    margin-left: 20px;
    padding-bottom: 12px;
  }
}

// 优化描述列表样式
:deep(.ant-descriptions) {
  .ant-descriptions-item-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-600);
  }

  .ant-descriptions-item-content {
    font-size: 13px;
  }
}

// 优化表单项间距
:deep(.ant-form) {
  .ant-form-item {
    margin-bottom: 16px;
  }
}

:deep(.ant-form-inline) {
  .ant-form-item {
    margin-right: 24px;
    margin-bottom: 16px;

    &:last-child {
      margin-right: 0;
    }
  }
}

// 优化模型配置表单项
.model-config-section {
  padding: 6px 8px;
  background: var(--gray-10);
  border-radius: 8px;
  border: 1px solid var(--gray-150);

  .ant-form-item {
    margin-bottom: 0;

    .ant-form-item-label {
      font-weight: 500;
    }

    .ant-form-item-extra {
      font-size: 12px;
      color: var(--gray-500);
      margin-top: 4px;
    }
  }

  // 为模型配置部分内的列添加特定样式
  .ant-col {
    &:not(:last-child) .ant-form-item {
      padding-right: 12px;
    }
  }
}

// 优化统计数字样式
:deep(.ant-row) {
  .ant-col {
    .ant-statistic {
      padding: 12px;
      border: 1px solid var(--gray-200);
      border-radius: 6px;
      text-align: center;
      transition: all 0.3s;

      &:hover {
        border-color: var(--gray-300);
        box-shadow: 0 2px 4px var(--shadow-1);
      }
    }
  }
}

// 检索指标样式
.retrieval-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 20px;
  align-items: center;
}

.metric-item {
  display: flex;
  align-items: center;
}

.metric-content {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.4;
  font-weight: 500;
  white-space: nowrap;

  &.metric-recall {
    background-color: var(--color-info-50);
    color: var(--color-info-900);
  }

  &.metric-precision {
    background-color: var(--color-success-50);
    color: var(--color-success-900);
  }

  &.metric-map,
  &.metric-ndcg {
    background-color: var(--color-accent-50);
    color: var(--color-accent-900);
  }
}

.metric-name {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.metric-value {
  font-weight: 700;
  font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
}

.no-metrics {
  color: var(--gray-400);
  font-style: italic;
}

// 答案推理样式
.answer-reasoning {
  font-size: 12px;
  color: var(--gray-600);
  margin-top: 8px;
  line-height: 1.4;
  cursor: pointer;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  word-wrap: break-word;
  overflow: hidden;
  text-overflow: ellipsis;

  &:hover {
    color: var(--gray-800);
  }
}

// 加载和空状态样式
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
}

.empty-results {
  padding: 40px 0;
  text-align: center;
}

// 评估报告样式
.evaluation-report {
  margin-bottom: 20px;

  .report-metric {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid var(--gray-100);

    &:last-child {
      border-bottom: none;
    }

    .metric-label {
      font-size: 14px;
      padding-right: 18px;
      color: var(--gray-700);
    }

    .metric-value {
      font-size: 14px;
      font-weight: 600;
      font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
    }
  }

  .accuracy-stats {
    .accuracy-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 0;
      border-bottom: 1px solid var(--gray-100);

      &:last-child {
        border-bottom: none;
      }

      .accuracy-label {
        font-size: 14px;
        color: var(--gray-700);
      }

      .accuracy-value {
        font-size: 16px;
        font-weight: 600;
        font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
      }
    }
  }

  :deep(.ant-card) {
    .ant-card-head {
      border-bottom: 1px solid var(--gray-200);

      .ant-card-head-title {
        font-size: 14px;
        font-weight: 500;
      }
    }
  }
}

// 历史评估记录区域
.history-section {
  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;

    .section-title {
      margin: 0;
      font-size: 14px;
      font-weight: 500;
      color: var(--gray-700);
    }

    .refresh-btn {
      color: var(--gray-600);
      border: none;
      box-shadow: none;
      padding: 4px 8px;
      height: auto;
      font-size: 13px;

      &:hover {
        color: var(--color-primary-600);
        background-color: var(--color-primary-50);
      }

      &:active {
        color: var(--color-primary-700);
        background-color: var(--color-primary-100);
      }

      .anticon {
        font-size: 14px;
      }
    }
  }

  :deep(.ant-table) {
    border: 1px solid var(--gray-100);
  }
}

// JSON 查看器样式
.json-viewer-container {
  max-height: 400px;
  overflow: auto;

  .json-viewer {
    margin: 0;
    padding: 0;
    font-family: 'SF Mono', 'Monaco', 'Consolas', 'Menlo', monospace;
    font-size: 13px;
    line-height: 1.5;
    color: var(--gray-800);
    white-space: pre-wrap;
    word-wrap: break-word;
    background: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 6px;
    padding: 12px;
  }
}

// 仅查看错误按钮样式
.error-only-active {
  background-color: var(--color-error-500) !important;
  border-color: var(--color-error-500) !important;
  color: white !important;

  &:hover {
    background-color: var(--color-error-600) !important;
    border-color: var(--color-error-600) !important;
  }

  &:focus {
    background-color: var(--color-error-500) !important;
    border-color: var(--color-error-500) !important;
  }
}
</style>
