<template>
  <a-modal
    :open="isOpen"
    title="后台作业"
    :width="680"
    :footer="null"
    :destroy-on-close="false"
    class="job-center-modal"
    @cancel="handleClose"
  >
    <a-alert
      type="info"
      show-icon
      class="job-tip"
      message="状态为「已完成」仅代表作业执行结束，其内部仍可能存在已捕获的问题，请留意日志。"
    />
    <div class="job-center">
      <div class="job-toolbar">
        <div class="job-filter-group">
          <a-segmented v-model:value="statusFilter" :options="jobFilterOptions" />
        </div>
        <div class="job-toolbar-actions">
          <a-button type="text" @click="handleRefresh" :loading="loadingState"> 刷新 </a-button>
        </div>
      </div>

      <a-alert
        v-if="lastErrorState"
        type="error"
        show-icon
        class="job-alert"
        :message="lastErrorState.message || '加载作业信息失败'"
      />

      <div v-if="hasJobs" class="job-list">
        <div
          v-for="job in filteredJobs"
          :key="job.id"
          class="job-card"
          :class="jobCardClasses(job)"
          @click="handleDetail(job.id)"
        >
          <!-- 状态指示器 -->
          <div class="job-card-status-indicator" :class="`status-${job.status}`">
            <span class="status-dot"></span>
            <span class="status-text">{{ statusLabel(job.status) }}</span>
          </div>

          <div class="job-card-header">
            <div class="job-card-info">
              <div class="job-card-title">{{ job.name }}</div>
              <div class="job-card-meta">
                <span class="job-card-type">{{ jobTypeLabel(job.type) }}</span>
                <span class="job-card-id">#{{ formatJobId(job.id) }}</span>
                <span v-if="getJobDuration(job)" class="job-card-duration">{{
                  getJobDuration(job)
                }}</span>
              </div>
            </div>
          </div>

          <!-- 进度信息 -->
          <div v-if="!isJobCompleted(job)" class="job-card-progress">
            <a-progress
              :percent="Math.round(job.progress || 0)"
              :status="progressStatus(job.status)"
              :stroke-width="4"
              :show-info="false"
            />
            <span class="progress-text">{{ Math.round(job.progress || 0) }}%</span>
          </div>
          <div v-if="job.message && !isJobCompleted(job)" class="job-card-message">
            {{ job.message }}
          </div>
          <div v-if="job.error" class="job-card-error">
            {{ job.error }}
          </div>

          <!-- 底部信息 -->
          <div class="job-card-footer">
            <div class="job-card-times">
              <span v-if="job.started_at">开始 {{ formatTime(job.started_at, 'short') }}</span>
              <span v-if="job.completed_at"
                >· 完成 {{ formatTime(job.completed_at, 'short') }}</span
              >
              <span v-if="!job.started_at">创建 {{ formatTime(job.created_at, 'short') }}</span>
            </div>
            <div class="job-card-actions">
              <a-button
                type="text"
                size="small"
                danger
                v-if="canCancel(job)"
                @click.stop="handleCancel(job.id)"
              >
                取消
              </a-button>
              <a-button
                type="text"
                size="small"
                danger
                v-if="isJobCompleted(job)"
                @click.stop="handleDelete(job.id, job.name)"
              >
                删除
              </a-button>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="job-empty">
        <div class="job-empty-icon">🗂️</div>
        <div class="job-empty-title">{{ emptyHint.title }}</div>
        <div class="job-empty-subtitle">{{ emptyHint.subtitle }}</div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, h, watch, ref } from 'vue'
import { Modal } from 'ant-design-vue'
import { useBackgroundJobsStore } from '@/modules/background-jobs/model/jobs'
import { storeToRefs } from 'pinia'
import { formatFullDateTime, formatRelative, parseToShanghai } from '@/shared/lib/time'

const jobsStore = useBackgroundJobsStore()
const {
  isDrawerOpen,
  sortedJobs,
  loading,
  lastError,
  activeCount,
  totalCount,
  successCount,
  failedCount
} = storeToRefs(jobsStore)
const isOpen = isDrawerOpen

const jobs = computed(() => sortedJobs.value)
const loadingState = computed(() => Boolean(loading.value))
const lastErrorState = computed(() => lastError.value)
const statusFilter = ref('all')
const inProgressCount = computed(() => activeCount.value || 0)
const completedCount = computed(() => successCount.value || 0)
const failedJobCount = computed(() => failedCount.value || 0)
const totalJobCount = computed(() => totalCount.value || 0)
const jobFilterOptions = computed(() => [
  {
    label: () =>
      h('span', { class: 'job-filter-option' }, [
        '全部',
        h('span', { class: 'filter-count' }, totalJobCount.value)
      ]),
    value: 'all'
  },
  {
    label: () =>
      h('span', { class: 'job-filter-option' }, [
        '进行中',
        h('span', { class: 'filter-count' }, inProgressCount.value)
      ]),
    value: 'active'
  },
  {
    label: () =>
      h('span', { class: 'job-filter-option' }, [
        '已完成',
        h('span', { class: 'filter-count' }, completedCount.value)
      ]),
    value: 'success'
  },
  {
    label: () =>
      h('span', { class: 'job-filter-option' }, [
        '失败',
        h('span', { class: 'filter-count' }, failedJobCount.value)
      ]),
    value: 'failed'
  }
])

const STATUS_CONFIG = {
  pending: { label: '等待中', terminal: false, cancelable: true, progress: 'active' },
  running: { label: '进行中', terminal: false, cancelable: true, progress: 'active' },
  success: { label: '已完成', terminal: true, cancelable: false, progress: 'success' },
  failed: { label: '失败', terminal: true, cancelable: false, progress: 'exception' },
  cancelled: { label: '已取消', terminal: true, cancelable: false, progress: 'normal' }
}
const TASK_TYPE_LABELS = {
  knowledge_ingest: '知识库导入',
  knowledge_parse: '文档解析',
  knowledge_index: '文档入库',
  knowledge_graph_index: '图谱构建',
  knowledge_document_reset: '文档产物撤销',
  dataset_generation: '评估集生成',
  rag_evaluation: 'RAG 评估'
}

const isActiveStatus = (status) => Boolean(STATUS_CONFIG[status]) && !STATUS_CONFIG[status].terminal
const isFailedStatus = (status) => status === 'failed'

const filteredJobs = computed(() => {
  const list = jobs.value
  switch (statusFilter.value) {
    case 'active':
      return list.filter((job) => isActiveStatus(job.status))
    case 'success':
      return list.filter((job) => job.status === 'success')
    case 'failed':
      return list.filter((job) => isFailedStatus(job.status))
    default:
      return list
  }
})

const hasJobs = computed(() => filteredJobs.value.length > 0)

const emptyHint = computed(() => {
  switch (statusFilter.value) {
    case 'active':
      return { title: '暂无进行中的作业', subtitle: '当前没有正在执行的后台作业。' }
    case 'success':
      return { title: '暂无已完成的作业', subtitle: '执行成功的后台作业会显示在这里。' }
    case 'failed':
      return { title: '暂无失败的作业', subtitle: '执行失败的后台作业会显示在这里。' }
    default:
      return {
        title: '暂无作业',
        subtitle: '提交知识库导入等后台作业后，将在这里展示实时进度（仅展示最近的 100 个作业）。'
      }
  }
})

function jobCardClasses(job) {
  return {
    'job-card--active': isActiveStatus(job.status),
    'job-card--success': job.status === 'success',
    'job-card--failed': job.status === 'failed'
  }
}

function jobTypeLabel(type) {
  if (!type) return '后台作业'
  return TASK_TYPE_LABELS[type] || type
}

function formatJobId(id) {
  if (!id) return '--'
  return id.slice(0, 8)
}

watch(
  isOpen,
  (open) => {
    if (open) {
      jobsStore.loadJobs()
    }
  },
  { immediate: true }
)

function handleClose() {
  jobsStore.closeDrawer()
}

function handleRefresh() {
  jobsStore.loadJobs()
}

function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const DETAIL_ROW_STYLE = 'display:flex;gap:8px;padding:3px 0;font-size:13px'
const DETAIL_LABEL_STYLE = 'color:var(--gray-500);min-width:64px;flex-shrink:0'
const DETAIL_TITLE_STYLE = 'font-weight:600;margin-top:12px;font-size:13px'
const DETAIL_JSON_STYLE =
  'max-height:240px;overflow:auto;background:var(--gray-50);padding:10px;border-radius:6px;' +
  'font-size:12px;white-space:pre-wrap;word-break:break-all;margin:4px 0 0'

function hasContent(value) {
  if (value === null || value === undefined) return false
  if (typeof value === 'object') return Object.keys(value).length > 0
  return true
}

async function handleDetail(jobId) {
  await jobsStore.refreshJob(jobId)
  const job = jobs.value.find((item) => item.id === jobId)
  if (!job) {
    return
  }
  const rows = [
    ['类型', jobTypeLabel(job.type)],
    ['状态', statusLabel(job.status)],
    ['进度', `${Math.round(job.progress || 0)}%`],
    ['创建时间', formatTime(job.created_at)],
    ['开始时间', job.started_at ? formatTime(job.started_at) : '-'],
    ['完成时间', job.completed_at ? formatTime(job.completed_at) : '-'],
    ['耗时', getJobDuration(job) || '-'],
    ['描述', job.message || '-'],
    ['错误', job.error || '-']
  ]
  const children = rows.map(([label, value]) =>
    h('div', { style: DETAIL_ROW_STYLE }, [
      h('span', { style: DETAIL_LABEL_STYLE }, label),
      h('span', value)
    ])
  )
  if (hasContent(job.result)) {
    children.push(h('div', { style: DETAIL_TITLE_STYLE }, '结果'))
    children.push(h('pre', { style: DETAIL_JSON_STYLE }, prettyJson(job.result)))
  }
  Modal.info({
    title: job.name,
    width: 560,
    content: h('div', children)
  })
}

function handleCancel(jobId) {
  jobsStore.cancelJob(jobId)
}

function handleDelete(jobId, jobName) {
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除作业"${jobName}"吗？此操作不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => {
      jobsStore.deleteJob(jobId)
    }
  })
}

function formatTime(value, mode = 'full') {
  if (!value) return '-'
  if (mode === 'short') {
    return formatRelative(value)
  }
  return formatFullDateTime(value)
}

function getJobDuration(job) {
  if (!job.started_at || !job.completed_at) return null
  try {
    const start = parseToShanghai(job.started_at)
    const end = parseToShanghai(job.completed_at)
    if (!start || !end) {
      return null
    }

    const diffSeconds = Math.max(0, Math.floor(end.diff(start, 'second')))
    const hours = Math.floor(diffSeconds / 3600)
    const minutes = Math.floor((diffSeconds % 3600) / 60)
    const seconds = diffSeconds % 60

    if (hours > 0) {
      return `${hours}小时${minutes}分钟`
    }
    if (minutes > 0) {
      return `${minutes}分钟${seconds}秒`
    }
    if (seconds > 0) {
      return `${seconds}秒`
    }
    return '小于1秒'
  } catch {
    return null
  }
}

function isJobCompleted(job) {
  return Boolean(STATUS_CONFIG[job.status]?.terminal)
}

function statusLabel(status) {
  return STATUS_CONFIG[status]?.label || status
}

function progressStatus(status) {
  return STATUS_CONFIG[status]?.progress || 'active'
}

function canCancel(job) {
  return Boolean(STATUS_CONFIG[job.status]?.cancelable) && !job.cancel_requested
}
</script>
<style scoped lang="less">
.job-center {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: min(70vh, 720px);
  min-height: 0;
  overflow: hidden;
}

.job-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
  flex-wrap: wrap;
}

.job-filter-group {
  flex-shrink: 0;
}

.job-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

:deep(.filter-count) {
  margin-left: 2px;
  font-size: 12px;
  color: var(--gray-400);
}

.job-toolbar-actions :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 10px;
}

.job-alert {
  margin-bottom: 4px;
}

.job-tip {
  margin-bottom: 12px;
}

.job-list {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
  padding-right: 4px;
}

.job-card {
  background: var(--gray-0);
  border: 1px solid var(--border-surface);
  border-radius: 10px;
  padding: 12px 16px;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  cursor: pointer;
}

.job-card:hover {
  border-color: var(--border-surface-hover);
  background: var(--gray-25);
}

/* 状态指示器 */
.job-card-status-indicator {
  position: absolute;
  top: 14px;
  right: 14px;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-pending .status-dot {
  background: var(--color-info-500);
}
.status-pending .status-text {
  color: var(--color-info-500);
}

.status-running .status-dot {
  background: var(--color-success-500);
  animation: pulse 1.5s ease-in-out infinite;
}
.status-running .status-text {
  color: var(--color-success-500);
}

.status-success .status-dot {
  background: var(--color-success-500);
}
.status-success .status-text {
  color: var(--color-success-500);
}

.status-failed .status-dot {
  background: var(--color-error-500);
}
.status-failed .status-text {
  color: var(--color-error-500);
}

.status-cancelled .status-dot {
  background: var(--gray-500);
}
.status-cancelled .status-text {
  color: var(--gray-600);
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.6;
    transform: scale(0.9);
  }
}

.job-card-header {
  padding-right: 80px; /* 为状态指示器留出空间 */
}

.job-card-info {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.job-card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--gray-900);
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
  overflow: hidden;
}

.job-card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--gray-500);
}

.job-card-id {
  font-family: 'SF Mono', 'Monaco', monospace;
  letter-spacing: 0.03em;
}

.job-card-type {
  font-size: 12px;
}

.job-card-duration {
  color: var(--gray-400);
}

.job-card-progress {
  display: flex;
  align-items: center;
  gap: 10px;
}

.job-card-progress :deep(.ant-progress) {
  flex: 1;
}

.progress-text {
  font-size: 12px;
  font-weight: 500;
  color: var(--gray-500);
  min-width: 36px;
  text-align: right;
}

.job-card-message,
.job-card-error {
  font-size: 13px;
  line-height: 1.45;
  border-radius: 6px;
  padding: 10px 12px;
}

.job-card-message {
  background: var(--gray-100);
  color: var(--gray-800);
}

.job-card-error {
  background: var(--color-error-50);
  color: var(--color-error-500);
}

.job-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 4px;
  border-top: 1px solid var(--gray-100);
}

.job-card-times {
  display: flex;
  gap: 6px;
  font-size: 12px;
  color: var(--gray-400);
}

.job-card-actions {
  display: flex;
  gap: 2px;
}

.job-card-actions :deep(.ant-btn) {
  height: 24px;
  padding: 0 10px;
  font-size: 12px;
  color: var(--gray-500);
}

.job-card-actions :deep(.ant-btn:hover) {
  color: var(--gray-700);
  background: var(--gray-50);
}

.job-empty {
  margin-top: 32px;
  padding: 40px 30px;
  border-radius: 16px;
  background: var(--gray-50);
  border: 1px dashed var(--gray-300);
  text-align: center;
  color: var(--gray-600);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

.job-empty-icon {
  font-size: 28px;
}

.job-empty-title {
  font-size: 16px;
  font-weight: 600;
}

.job-empty-subtitle {
  font-size: 13px;
  max-width: 320px;
  line-height: 1.5;
  color: var(--gray-400);
}
</style>
