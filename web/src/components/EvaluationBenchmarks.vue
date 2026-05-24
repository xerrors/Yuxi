<template>
  <div class="evaluation-benchmarks-container">
    <!-- 操作栏 -->
    <div class="benchmarks-header">
      <div class="header-left">
        <span class="total-count">{{ benchmarks.length }} 个基准</span>
      </div>
      <div class="header-right">
        <a-button class="lucide-icon-btn" @click="loadBenchmarks">
          <template #icon><RefreshCw :size="16" /></template>
          刷新
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" @click="showUploadModal">
          <template #icon><Upload :size="16" /></template>
          上传基准
        </a-button>
        <a-button class="lucide-icon-btn" @click="showGenerateModal">
          <template #icon><Bot :size="16" /></template>
          自动生成
        </a-button>
      </div>
    </div>

    <!-- 基准列表 -->
    <div class="benchmarks-list">
      <div v-if="!loading && benchmarks.length === 0" class="empty-state">
        <div class="empty-icon">📋</div>
        <div class="empty-title">暂无评估基准</div>
        <div class="empty-description">上传或生成评估基准开始使用</div>
      </div>

      <div v-else-if="loading" class="loading-state">
        <a-spin size="large" />
      </div>

      <div v-else class="benchmark-list-content">
        <div
          v-for="benchmark in benchmarks"
          :key="benchmark.benchmark_id"
          class="benchmark-item"
          @click="previewBenchmark(benchmark)"
        >
          <!-- 主要内容 -->
          <div class="benchmark-main">
            <div class="benchmark-header">
              <h4 class="benchmark-name">{{ benchmark.name }}</h4>
              <div class="benchmark-actions">
                <a-button
                  type="text"
                  size="small"
                  class="lucide-icon-btn"
                  @click.stop="previewBenchmark(benchmark)"
                >
                  <Eye :size="15" />
                </a-button>
                <a-button
                  type="text"
                  size="small"
                  class="lucide-icon-btn"
                  :loading="!!downloadingBenchmarkMap[benchmark.benchmark_id]"
                  @click.stop="downloadBenchmark(benchmark)"
                >
                  <Download :size="15" />
                </a-button>
                <a-button
                  type="text"
                  size="small"
                  class="lucide-icon-btn"
                  danger
                  @click.stop="deleteBenchmark(benchmark)"
                >
                  <Trash2 :size="15" />
                </a-button>
              </div>
            </div>

            <p class="benchmark-desc">{{ benchmark.description || '暂无描述' }}</p>

            <!-- 标签区域 -->
            <div class="benchmark-meta">
              <div class="meta-row">
                <span
                  v-if="benchmark.has_gold_chunks && benchmark.has_gold_answers"
                  class="card-tag benchmark-tag tag-purple"
                >
                  检索 + 问答
                </span>
                <span v-else-if="benchmark.has_gold_chunks" class="card-tag benchmark-tag tag-blue">
                  检索评估
                </span>
                <span
                  v-else-if="benchmark.has_gold_answers"
                  class="card-tag benchmark-tag tag-gold"
                >
                  问答评估
                </span>
                <span v-else class="card-tag benchmark-tag">仅查询</span>

                <span v-if="benchmark.has_gold_chunks" class="card-tag benchmark-tag tag-green">
                  Gold Chunks
                </span>
                <span v-if="benchmark.has_gold_answers" class="card-tag benchmark-tag tag-green">
                  Gold Answer
                </span>
              </div>
            </div>
          </div>

          <!-- 底部信息 -->
          <div class="benchmark-footer">
            <span class="benchmark-time">{{ formatDate(benchmark.created_at) }}</span>
            <span class="benchmark-count">{{ benchmark.question_count }} 个问题</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 上传模态框 -->
    <BenchmarkUploadModal
      v-model:visible="uploadModalVisible"
      :database-id="databaseId"
      @success="onUploadSuccess"
    />

    <!-- 生成模态框 -->
    <BenchmarkGenerateModal
      v-model:visible="generateModalVisible"
      :database-id="databaseId"
      @success="onGenerateSuccess"
    />

    <!-- 预览模态框 -->
    <a-modal v-model:open="previewModalVisible" title="评估基准详情" width="1200px" :footer="null">
      <div v-if="previewData" class="preview-content">
        <div class="preview-header">
          <h3>{{ previewData.name }}</h3>
          <div class="preview-meta">
            <span class="meta-item">
              <span class="meta-label">问题数:</span>
              {{ previewData.question_count }}
            </span>
            <span class="meta-item">
              <span class="meta-label">Gold Chunks:</span>
              <span :class="previewData.has_gold_chunks ? 'status-yes' : 'status-no'">
                {{ previewData.has_gold_chunks ? '有' : '无' }}
              </span>
            </span>
            <span class="meta-item">
              <span class="meta-label">Gold Answer:</span>
              <span :class="previewData.has_gold_answers ? 'status-yes' : 'status-no'">
                {{ previewData.has_gold_answers ? '有' : '无' }}
              </span>
            </span>
          </div>
        </div>

        <div class="preview-questions" v-if="previewQuestions && previewQuestions.length > 0">
          <h4>问题列表 (共{{ previewPagination.total }}条)</h4>
          <a-table
            :dataSource="previewQuestions"
            :columns="displayedQuestionColumns"
            :pagination="paginationConfig"
            size="small"
            :rowKey="(_, index) => index"
            :loading="previewPagination.loading"
          >
            <template #bodyCell="{ column, record, index }">
              <template v-if="column.key === 'index'">
                <span class="question-num"
                  >Q{{
                    (previewPagination.current - 1) * previewPagination.pageSize + index + 1
                  }}</span
                >
              </template>
              <template v-if="column.key === 'query'">
                <a-tooltip :title="record?.query || ''" placement="topLeft">
                  <div class="question-text">{{ record?.query || '' }}</div>
                </a-tooltip>
              </template>
              <template v-if="column.key === 'gold_chunk_ids'">
                <a-tooltip
                  v-if="record?.gold_chunk_ids && record.gold_chunk_ids.length > 0"
                  :title="record.gold_chunk_ids.join(', ')"
                  placement="topLeft"
                >
                  <div class="question-chunk">
                    {{ record.gold_chunk_ids.slice(0, 3).join(', ') }}
                    <span v-if="record.gold_chunk_ids.length > 3"
                      >...等{{ record.gold_chunk_ids.length }}个</span
                    >
                  </div>
                </a-tooltip>
                <span v-else class="no-data">-</span>
              </template>
              <template v-if="column.key === 'gold_answer'">
                <a-tooltip
                  v-if="record?.gold_answer"
                  :title="record.gold_answer"
                  placement="topLeft"
                >
                  <div class="question-answer">
                    {{ record.gold_answer }}
                  </div>
                </a-tooltip>
                <span v-else class="no-data">-</span>
              </template>
            </template>
          </a-table>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Bot, Download, Eye, RefreshCw, Trash2, Upload } from 'lucide-vue-next'
import { evaluationApi } from '@/apis/knowledge_api'
import { useTaskerStore } from '@/stores/tasker'
import BenchmarkUploadModal from './modals/BenchmarkUploadModal.vue'
import BenchmarkGenerateModal from './modals/BenchmarkGenerateModal.vue'

const props = defineProps({
  databaseId: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['refresh'])

const taskerStore = useTaskerStore()

// 状态
const loading = ref(false)
const benchmarks = ref([])
const uploadModalVisible = ref(false)
const generateModalVisible = ref(false)
const previewModalVisible = ref(false)
const previewData = ref(null)
const previewQuestions = ref([])
const downloadingBenchmarkMap = reactive({})
const previewPagination = ref({
  current: 1,
  pageSize: 10,
  total: 0,
  loading: false
})

// 表格列定义
const questionColumns = [
  {
    title: '#',
    key: 'index',
    width: 60,
    align: 'center'
  },
  {
    title: '问题',
    dataIndex: 'query',
    key: 'query',
    width: 280,
    ellipsis: false
  },
  {
    title: 'Gold Chunks',
    dataIndex: 'gold_chunk_ids',
    key: 'gold_chunk_ids',
    width: 200,
    ellipsis: false
  },
  {
    title: 'Gold Answer',
    dataIndex: 'gold_answer',
    key: 'gold_answer',
    width: 420,
    ellipsis: false
  }
]

const displayedQuestionColumns = computed(() => {
  if (previewData.value && previewData.value.has_gold_chunks === false) {
    return questionColumns.filter((c) => c.key !== 'gold_chunk_ids')
  }
  return questionColumns
})

// 分页配置
const paginationConfig = computed(() => ({
  current: previewPagination.value.current,
  pageSize: previewPagination.value.pageSize,
  total: previewPagination.value.total,
  showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
  showSizeChanger: true,
  pageSizeOptions: ['5', '10', '20', '50'],
  showQuickJumper: true,
  size: 'small',
  onChange: handlePageChange,
  onShowSizeChange: handlePageSizeChange
}))

// 加载基准列表
const loadBenchmarks = async () => {
  if (!props.databaseId) return

  loading.value = true
  try {
    const response = await evaluationApi.getBenchmarks(props.databaseId)

    if (response && response.message === 'success' && Array.isArray(response.data)) {
      benchmarks.value = response.data
    } else {
      console.error('响应格式不符合预期:', response)
      message.error('基准数据格式错误')
    }
  } catch (error) {
    console.error('加载评估基准失败:', error)
    message.error('加载评估基准失败')
  } finally {
    loading.value = false
  }
}

// 显示上传模态框
const showUploadModal = () => {
  uploadModalVisible.value = true
}

// 显示生成模态框
const showGenerateModal = () => {
  generateModalVisible.value = true
}

// 上传成功回调
const onUploadSuccess = () => {
  loadBenchmarks()
  message.success('基准上传成功')
  taskerStore.loadTasks() // 刷新任务列表
  // 通知父组件刷新基准列表
  emit('refresh')
}

// 生成成功回调
const onGenerateSuccess = () => {
  loadBenchmarks()
  // message.success('基准生成成功'); // 移除，由模态框提示任务提交
  taskerStore.loadTasks() // 刷新任务列表
  // 通知父组件刷新基准列表
  emit('refresh')
}

// 分页处理函数
const handlePageChange = (page, pageSize) => {
  previewPagination.value.current = page
  previewPagination.value.pageSize = pageSize
  loadPreviewQuestions()
}

const handlePageSizeChange = (current, size) => {
  previewPagination.value.current = 1
  previewPagination.value.pageSize = size
  loadPreviewQuestions()
}

// 加载预览问题（分页）
const loadPreviewQuestions = async () => {
  if (!previewData.value?.benchmark_id) return

  try {
    previewPagination.value.loading = true
    const response = await evaluationApi.getBenchmarkByDb(
      props.databaseId,
      previewData.value.benchmark_id,
      previewPagination.value.current,
      previewPagination.value.pageSize
    )

    if (response.message === 'success') {
      previewQuestions.value = response.data.questions || []
      previewPagination.value.total = response.data.pagination?.total_questions || 0
    }
  } catch (error) {
    console.error('加载预览问题失败:', error)
    message.error('加载预览问题失败')
  } finally {
    previewPagination.value.loading = false
  }
}

// 预览基准
const previewBenchmark = async (benchmark) => {
  try {
    // 重置分页状态
    previewPagination.value = {
      current: 1,
      pageSize: 10,
      total: 0,
      loading: false
    }

    const response = await evaluationApi.getBenchmarkByDb(
      props.databaseId,
      benchmark.benchmark_id,
      previewPagination.value.current,
      previewPagination.value.pageSize
    )

    if (response.message === 'success') {
      // 保存基准ID用于后续分页请求
      previewData.value = {
        ...response.data,
        benchmark_id: benchmark.benchmark_id // 手动添加benchmark_id
      }
      previewQuestions.value = response.data.questions || []
      previewPagination.value.total = response.data.pagination?.total_questions || 0
      console.log('预览问题数据:', response.data.questions) // 调试信息
      previewModalVisible.value = true
    }
  } catch (error) {
    console.error('获取基准详情失败:', error)
    message.error('获取基准详情失败')
  }
}

const parseDownloadFilename = (contentDisposition) => {
  if (!contentDisposition) return ''

  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match && utf8Match[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch (error) {
      console.warn('解析 UTF-8 文件名失败:', error)
    }
  }

  const asciiMatch = contentDisposition.match(/filename="?([^";]+)"?/i)
  if (asciiMatch && asciiMatch[1]) {
    return asciiMatch[1]
  }

  return ''
}

// 下载基准
const downloadBenchmark = async (benchmark) => {
  const benchmarkId = benchmark?.benchmark_id
  if (!benchmarkId) return
  if (downloadingBenchmarkMap[benchmarkId]) return

  downloadingBenchmarkMap[benchmarkId] = true
  try {
    const response = await evaluationApi.downloadBenchmark(benchmarkId)
    const blob = await response.blob()
    const contentDisposition =
      response.headers.get('Content-Disposition') || response.headers.get('content-disposition')
    const headerFilename = parseDownloadFilename(contentDisposition)
    const fallbackFilename = `${benchmark.name || benchmarkId}.jsonl`
    const filename = headerFilename || fallbackFilename

    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)

    message.success('下载成功')
  } catch (error) {
    console.error('下载基准失败:', error)
    message.error(`下载失败: ${error.message || '未知错误'}`)
  } finally {
    delete downloadingBenchmarkMap[benchmarkId]
  }
}

// 删除基准
const deleteBenchmark = (benchmark) => {
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除评估基准"${benchmark.name}"吗？此操作不可恢复。`,
    okText: '确定',
    cancelText: '取消',
    onOk: async () => {
      try {
        const response = await evaluationApi.deleteBenchmark(benchmark.benchmark_id)
        if (response.message === 'success') {
          message.success('删除成功')
          loadBenchmarks()
        }
      } catch (error) {
        console.error('删除基准失败:', error)
        message.error('删除基准失败')
      }
    }
  })
}

// 格式化日期
const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

// 组件挂载时加载数据
onMounted(() => {
  loadBenchmarks()
})
</script>

<style lang="less" scoped>
.evaluation-benchmarks-container {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.benchmarks-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  margin-bottom: 12px;

  .total-count {
    font-size: 13px;
    color: var(--color-text-secondary);
  }

  .header-right {
    display: flex;
    gap: 8px;
  }
}

.benchmarks-list {
  flex: 1;
  overflow-y: auto;
}

.benchmark-list-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.benchmark-item {
  padding: 12px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--color-bg-container);
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: var(--color-primary-100);
    box-shadow: 0 1px 2px var(--shadow-2);
    background: var(--gray-10);
  }

  &:active {
    transform: scale(0.998);
  }
}

.benchmark-main {
  margin-bottom: 8px;
}

.benchmark-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 6px;

  .benchmark-name {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--gray-1000);
    flex: 1;
  }

  .benchmark-actions {
    display: flex;
    gap: 4px;
  }
}

.benchmark-desc {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.benchmark-meta {
  margin-bottom: 8px;
}

.meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.benchmark-tag {
  min-height: 22px;
  padding: 0 8px;
  border-radius: 4px;
}

.benchmark-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid var(--gray-150);
  font-size: 11px;
  color: var(--color-text-tertiary);

  .benchmark-id {
    font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
  }

  .benchmark-count {
    color: var(--color-primary-700);
    font-weight: 500;
  }
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 300px;
  text-align: center;

  .empty-icon {
    font-size: 48px;
    margin-bottom: 16px;
    opacity: 0.5;
  }

  .empty-title {
    font-size: 18px;
    font-weight: 500;
    color: var(--gray-900);
    margin-bottom: 8px;
  }

  .empty-description {
    font-size: 14px;
    color: var(--gray-600);
  }
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
}

// 预览模态框样式
.preview-content {
  .preview-header {
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--gray-200);

    h3 {
      margin: 0 0 12px;
      font-size: 20px;
      font-weight: 600;
      color: var(--gray-1000);
    }

    .preview-meta {
      display: flex;
      gap: 24px;

      .meta-item {
        font-size: 14px;

        .meta-label {
          color: var(--color-text-tertiary);
          margin-right: 4px;
        }

        .status-yes {
          color: var(--color-success-700);
          font-weight: 500;
        }

        .status-no {
          color: var(--color-text-tertiary);
        }
      }
    }
  }

  .preview-questions {
    h4 {
      margin: 0 0 16px;
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-900);
    }

    .question-num {
      font-size: 14px;
      font-weight: 600;
      color: var(--gray-700);
    }

    .question-text {
      font-size: 14px;
      line-height: 1.5;
      color: var(--gray-800);
      word-break: break-all;
      display: -webkit-box;
      -webkit-line-clamp: 4;
      -webkit-box-orient: vertical;
      overflow: hidden;
      max-height: 6em; // 4行 * 1.5em line-height
      cursor: pointer;
    }

    .question-chunk,
    .question-answer {
      font-size: 13px;
      color: var(--gray-600);
      word-break: break-all;
      display: -webkit-box;
      -webkit-line-clamp: 4;
      -webkit-box-orient: vertical;
      overflow: hidden;
      max-height: 6em; // 4行 * 1.5em line-height for 13px font
      cursor: pointer;
    }

    .no-data {
      color: var(--gray-400);
      font-style: italic;
    }

    :deep(.ant-table) {
      .ant-table-thead > tr > th {
        background-color: var(--gray-50);
        border-bottom: 1px solid var(--gray-200);
        font-weight: 600;
        font-size: 13px;
        padding: 8px 12px;
        white-space: nowrap;
      }

      .ant-table-tbody > tr > td {
        padding: 8px 12px;
        border-bottom: 1px solid var(--gray-150);
        font-size: 13px;
        vertical-align: top;
        line-height: 1.4;
      }

      .ant-table-tbody > tr:hover > td {
        background-color: var(--gray-50);
      }

      // 确保表格单元格内容可以换行
      .ant-table-cell {
        white-space: normal !important;
        word-wrap: break-word !important;
        word-break: break-all !important;
      }
    }
  }
}
</style>
