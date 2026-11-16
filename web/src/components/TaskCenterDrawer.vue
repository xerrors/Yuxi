<template>
  <a-modal
    :open="isOpen"
    title="任务中心"
    :width="680"
    :footer="null"
    :destroy-on-close="false"
    class="task-center-modal"
    @cancel="handleClose"
  >
    <a-alert
      type="info"
      show-icon
      class="task-tip"
      message="状态为「已完成」仅代表任务执行结束，其内部仍可能存在已捕获的问题，请留意日志。"
    />
    <div class="task-center">
      <div class="task-toolbar">
        <div class="task-filter-group">
          <a-segmented v-model:value="statusFilter" :options="taskFilterOptions" />
        </div>
        <div class="task-toolbar-actions">
          <a-button type="text" @click="handleRefresh" :loading="loadingState"> 刷新 </a-button>
        </div>
      </div>

      <a-alert
        v-if="lastErrorState"
        type="error"
        show-icon
        class="task-alert"
        :message="lastErrorState.message || '加载任务信息失败'"
      />

      <div v-if="hasTasks" class="task-list">
        <div
          v-for="task in filteredTasks"
          :key="task.id"
          class="task-card"
          :class="taskCardClasses(task)"
          @click="handleDetail(task.id)"
        >
          <!-- 状态指示器 -->
          <div class="task-card-status-indicator" :class="`status-${task.status}`">
            <span class="status-dot"></span>
            <span class="status-text">{{ statusLabel(task.status) }}</span>
          </div>

          <div class="task-card-header">
            <div class="task-card-info">
              <div class="task-card-title">{{ task.name }}</div>
              <div class="task-card-meta">
                <span class="task-card-type">{{ taskTypeLabel(task.type) }}</span>
                <span class="task-card-id">#{{ formatTaskId(task.id) }}</span>
                <span v-if="getTaskDuration(task)" class="task-card-duration">{{
                  getTaskDuration(task)
                }}</span>
              </div>
            </div>
          </div>

          <!-- 进度信息 -->
          <div v-if="!isTaskCompleted(task)" class="task-card-progress">
            <a-progress
              :percent="Math.round(task.progress || 0)"
              :status="progressStatus(task.status)"
              :stroke-width="4"
              :show-info="false"
            />
            <span class="progress-text">{{ Math.round(task.progress || 0) }}%</span>
          </div>
          <div v-if="task.message && !isTaskCompleted(task)" class="task-card-message">
            {{ task.message }}
          </div>
          <div v-if="task.error" class="task-card-error">
            {{ task.error }}
          </div>

          <!-- 底部信息 -->
          <div class="task-card-footer">
            <div class="task-card-times">
              <span v-if="task.started_at">开始 {{ formatTime(task.started_at, 'short') }}</span>
              <span v-if="task.completed_at"
                >· 完成 {{ formatTime(task.completed_at, 'short') }}</span
              >
              <span v-if="!task.started_at">创建 {{ formatTime(task.created_at, 'short') }}</span>
            </div>
            <div class="task-card-actions">
              <a-button
                type="text"
                size="small"
                danger
                v-if="canCancel(task)"
                @click.stop="handleCancel(task.id)"
              >
                取消
              </a-button>
              <a-button
                type="text"
                size="small"
                danger
                v-if="isTaskCompleted(task)"
                @click.stop="handleDelete(task.id, task.name)"
              >
                删除
              </a-button>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="task-empty">
        <div class="task-empty-icon">🗂️</div>
        <div class="task-empty-title">{{ emptyHint.title }}</div>
        <div class="task-empty-subtitle">{{ emptyHint.subtitle }}</div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, h, watch, ref } from 'vue'
import { Modal } from 'ant-design-vue'
import { useTaskerStore } from '@/stores/tasker'
import { storeToRefs } from 'pinia'
import { formatFullDateTime, formatRelative, parseToShanghai } from '@/utils/time'

const taskerStore = useTaskerStore()
const {
  isDrawerOpen,
  sortedTasks,
  loading,
  lastError,
  activeCount,
  totalCount,
  successCount,
  failedCount
} = storeToRefs(taskerStore)
const isOpen = isDrawerOpen

const tasks = computed(() => sortedTasks.value)
const loadingState = computed(() => Boolean(loading.value))
const lastErrorState = computed(() => lastError.value)
const statusFilter = ref('all')
const inProgressCount = computed(() => activeCount.value || 0)
const completedCount = computed(() => successCount.value || 0)
const failedTaskCount = computed(() => failedCount.value || 0)
const totalTaskCount = computed(() => totalCount.value || 0)
const taskFilterOptions = computed(() => [
  {
    label: () =>
      h('span', { class: 'task-filter-option' }, [
        '全部',
        h('span', { class: 'filter-count' }, totalTaskCount.value)
      ]),
    value: 'all'
  },
  {
    label: () =>
      h('span', { class: 'task-filter-option' }, [
        '进行中',
        h('span', { class: 'filter-count' }, inProgressCount.value)
      ]),
    value: 'active'
  },
  {
    label: () =>
      h('span', { class: 'task-filter-option' }, [
        '已完成',
        h('span', { class: 'filter-count' }, completedCount.value)
      ]),
    value: 'success'
  },
  {
    label: () =>
      h('span', { class: 'task-filter-option' }, [
        '失败',
        h('span', { class: 'filter-count' }, failedTaskCount.value)
      ]),
    value: 'failed'
  }
])

const STATUS_CONFIG = {
  pending: { label: '等待中', terminal: false, cancelable: true, progress: 'active' },
  queued: { label: '已排队', terminal: false, cancelable: true, progress: 'active' },
  running: { label: '进行中', terminal: false, cancelable: true, progress: 'active' },
  success: { label: '已完成', terminal: true, cancelable: false, progress: 'success' },
  failed: { label: '失败', terminal: true, cancelable: false, progress: 'exception' },
  cancelled: { label: '已取消', terminal: true, cancelable: false, progress: 'normal' }
}
const TASK_TYPE_LABELS = {
  knowledge_ingest: '知识库导入',
  knowledge_parse: '文档解析',
  knowledge_index: '文档入库',
  knowledge_reslice: '文档重切',
  knowledge_graph_index: '图谱构建',
  dataset_generation: '评估集生成',
  rag_evaluation: 'RAG 评估'
}

const isActiveStatus = (status) => Boolean(STATUS_CONFIG[status]) && !STATUS_CONFIG[status].terminal
const isFailedStatus = (status) => status === 'failed' || status === 'cancelled'

const filteredTasks = computed(() => {
  const list = tasks.value
  switch (statusFilter.value) {
    case 'active':
      return list.filter((task) => isActiveStatus(task.status))
    case 'success':
      return list.filter((task) => task.status === 'success')
    case 'failed':
      return list.filter((task) => isFailedStatus(task.status))
    default:
      return list
  }
})

const hasTasks = computed(() => filteredTasks.value.length > 0)

const emptyHint = computed(() => {
  switch (statusFilter.value) {
    case 'active':
      return { title: '暂无进行中的任务', subtitle: '当前没有正在执行的后台任务。' }
    case 'success':
      return { title: '暂无已完成的任务', subtitle: '执行成功的后台任务会显示在这里。' }
    case 'failed':
      return { title: '暂无失败的任务', subtitle: '失败或已取消的后台任务会显示在这里。' }
    default:
      return {
        title: '暂无任务',
        subtitle: '提交知识库导入等后台任务后，将在这里展示实时进度（仅展示最近的 100 个任务）。'
      }
  }
})

function taskCardClasses(task) {
  return {
    'task-card--active': isActiveStatus(task.status),
    'task-card--success': task.status === 'success',
    'task-card--failed': task.status === 'failed'
  }
}

function taskTypeLabel(type) {
  if (!type) return '后台任务'
  return TASK_TYPE_LABELS[type] || type
}

function formatTaskId(id) {
  if (!id) return '--'
  return id.slice(0, 8)
}

watch(
  isOpen,
  (open) => {
    if (open) {
      taskerStore.loadTasks()
    }
  },
  { immediate: true }
)

function handleClose() {
  taskerStore.closeDrawer()
}

function handleRefresh() {
  taskerStore.loadTasks()
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

function handleDetail(taskId) {
  const task = tasks.value.find((item) => item.id === taskId)
  if (!task) {
    return
  }
  const rows = [
    ['类型', taskTypeLabel(task.type)],
    ['状态', statusLabel(task.status)],
    ['进度', `${Math.round(task.progress || 0)}%`],
    ['创建时间', formatTime(task.created_at)],
    ['开始时间', task.started_at ? formatTime(task.started_at) : '-'],
    ['完成时间', task.completed_at ? formatTime(task.completed_at) : '-'],
    ['耗时', getTaskDuration(task) || '-'],
    ['描述', task.message || '-'],
    ['错误', task.error || '-']
  ]
  const children = rows.map(([label, value]) =>
    h('div', { style: DETAIL_ROW_STYLE }, [
      h('span', { style: DETAIL_LABEL_STYLE }, label),
      h('span', value)
    ])
  )
  if (hasContent(task.payload)) {
    children.push(h('div', { style: DETAIL_TITLE_STYLE }, '参数'))
    children.push(h('pre', { style: DETAIL_JSON_STYLE }, prettyJson(task.payload)))
  }
  if (hasContent(task.result)) {
    children.push(h('div', { style: DETAIL_TITLE_STYLE }, '结果'))
    children.push(h('pre', { style: DETAIL_JSON_STYLE }, prettyJson(task.result)))
  }
  Modal.info({
    title: task.name,
    width: 560,
    content: h('div', children)
  })
}

function handleCancel(taskId) {
  taskerStore.cancelTask(taskId)
}

function handleDelete(taskId, taskName) {
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除任务"${taskName}"吗？此操作不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => {
      taskerStore.deleteTask(taskId)
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

function getTaskDuration(task) {
  if (!task.started_at || !task.completed_at) return null
  try {
    const start = parseToShanghai(task.started_at)
    const end = parseToShanghai(task.completed_at)
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

function isTaskCompleted(task) {
  return Boolean(STATUS_CONFIG[task.status]?.terminal)
}

function statusLabel(status) {
  return STATUS_CONFIG[status]?.label || status
}

function progressStatus(status) {
  return STATUS_CONFIG[status]?.progress || 'active'
}

function canCancel(task) {
  return Boolean(STATUS_CONFIG[task.status]?.cancelable) && !task.cancel_requested
}
</script>
<style scoped lang="less">
.task-center {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: min(70vh, 720px);
  min-height: 0;
  overflow: hidden;
}

.task-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
  flex-wrap: wrap;
}

.task-filter-group {
  flex-shrink: 0;
}

.task-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

:deep(.filter-count) {
  margin-left: 2px;
  font-size: 12px;
  color: var(--gray-400);
}

.task-toolbar-actions :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 10px;
}

.task-alert {
  margin-bottom: 4px;
}

.task-tip {
  margin-bottom: 12px;
}

.task-list {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
  padding-right: 4px;
}

.task-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  padding: 12px 16px;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  cursor: pointer;
}

.task-card:hover {
  border-color: var(--gray-300);
  box-shadow: 0 2px 8px var(--shadow-1);
}

/* 状态指示器 */
.task-card-status-indicator {
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

.status-queued .status-dot {
  background: var(--color-info-500);
}
.status-queued .status-text {
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

.task-card-header {
  padding-right: 80px; /* 为状态指示器留出空间 */
}

.task-card-info {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.task-card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--gray-900);
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
  overflow: hidden;
}

.task-card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--gray-500);
}

.task-card-id {
  font-family: 'SF Mono', 'Monaco', monospace;
  letter-spacing: 0.03em;
}

.task-card-type {
  font-size: 12px;
}

.task-card-duration {
  color: var(--gray-400);
}

.task-card-progress {
  display: flex;
  align-items: center;
  gap: 10px;
}

.task-card-progress :deep(.ant-progress) {
  flex: 1;
}

.progress-text {
  font-size: 12px;
  font-weight: 500;
  color: var(--gray-500);
  min-width: 36px;
  text-align: right;
}

.task-card-message,
.task-card-error {
  font-size: 13px;
  line-height: 1.45;
  border-radius: 6px;
  padding: 10px 12px;
}

.task-card-message {
  background: var(--gray-100);
  color: var(--gray-800);
}

.task-card-error {
  background: var(--color-error-50);
  color: var(--color-error-500);
}

.task-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 4px;
  border-top: 1px solid var(--gray-100);
}

.task-card-times {
  display: flex;
  gap: 6px;
  font-size: 12px;
  color: var(--gray-400);
}

.task-card-actions {
  display: flex;
  gap: 2px;
}

.task-card-actions :deep(.ant-btn) {
  height: 24px;
  padding: 0 10px;
  font-size: 12px;
  color: var(--gray-500);
}

.task-card-actions :deep(.ant-btn:hover) {
  color: var(--gray-700);
  background: var(--gray-50);
}

.task-empty {
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

.task-empty-icon {
  font-size: 28px;
}

.task-empty-title {
  font-size: 16px;
  font-weight: 600;
}

.task-empty-subtitle {
  font-size: 13px;
  max-width: 320px;
  line-height: 1.5;
  color: var(--gray-400);
}
</style>
