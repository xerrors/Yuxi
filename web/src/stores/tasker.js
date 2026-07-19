import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { taskerApi } from '@/apis/tasker'
import { useUserStore } from '@/stores/user'
import { parseToShanghai } from '@/utils/time'

const ACTIVE_STATUSES = new Set(['pending', 'running', 'queued'])
const FAILED_STATUSES = new Set(['failed', 'cancelled'])

const createDefaultSummary = () => ({
  total: 0,
  filtered_total: 0,
  status_counts: {},
  type_counts: {}
})

const toTask = (raw = {}) => ({
  id: raw.id,
  name: raw.name || '后台任务',
  type: raw.type || 'general',
  status: raw.status || 'pending',
  progress: raw.progress ?? 0,
  message: raw.message || '',
  created_at: raw.created_at,
  updated_at: raw.updated_at,
  started_at: raw.started_at,
  completed_at: raw.completed_at,
  payload: raw.payload || {},
  result: raw.result,
  error: raw.error,
  cancel_requested: raw.cancel_requested || false
})

export const useTaskerStore = defineStore('tasker', () => {
  const userStore = useUserStore()
  const tasks = ref([])
  const loading = ref(false)
  const lastError = ref(null)
  const isDrawerOpen = ref(false)
  const summary = ref(createDefaultSummary())
  let pollingTimer = null

  const sortedTasks = computed(() => {
    return [...tasks.value].sort((a, b) => {
      const timeA = parseToShanghai(a.created_at)
      const timeB = parseToShanghai(b.created_at)
      if (!timeA && !timeB) return 0
      if (!timeA) return 1
      if (!timeB) return -1
      return timeB.valueOf() - timeA.valueOf()
    })
  })

  const statusCounts = computed(() => summary.value?.status_counts || {})

  const activeCount = computed(() =>
    Array.from(ACTIVE_STATUSES).reduce(
      (count, status) => count + (statusCounts.value?.[status] || 0),
      0
    )
  )
  const failedCount = computed(() =>
    Array.from(FAILED_STATUSES).reduce(
      (count, status) => count + (statusCounts.value?.[status] || 0),
      0
    )
  )
  const successCount = computed(() => statusCounts.value?.success || 0)
  const totalCount = computed(() => summary.value?.total || 0)

  // 是否存在需要持续轮询的任务：summary 统计或本地乐观登记的活跃任务
  const hasActiveTasks = computed(
    () => activeCount.value > 0 || tasks.value.some((task) => ACTIVE_STATUSES.has(task.status))
  )

  function upsertTask(rawTask) {
    if (!rawTask || !rawTask.id) return
    const task = toTask(rawTask)
    const index = tasks.value.findIndex((item) => item.id === task.id)
    if (index >= 0) {
      tasks.value.splice(index, 1, { ...tasks.value[index], ...task })
    } else {
      tasks.value.unshift(task)
    }
  }

  async function loadTasks(params = {}) {
    if (!userStore.isAdmin) {
      tasks.value = []
      summary.value = createDefaultSummary()
      lastError.value = null
      syncPolling()
      return
    }

    loading.value = true
    lastError.value = null
    try {
      const response = await taskerApi.fetchTasks(params)
      const taskList = response?.tasks || []
      summary.value = {
        ...createDefaultSummary(),
        ...(response?.summary || {})
      }
      tasks.value = taskList.map(toTask)
    } catch (error) {
      console.error('加载任务列表失败', error)
      lastError.value = error
      summary.value = createDefaultSummary()
    } finally {
      loading.value = false
      syncPolling()
    }
  }

  async function refreshTask(taskId) {
    if (!taskId) return
    try {
      const response = await taskerApi.fetchTaskDetail(taskId)
      if (response?.task) {
        upsertTask(response.task)
      }
    } catch (error) {
      console.error(`刷新任务 ${taskId} 详情失败`, error)
      lastError.value = error
    }
  }

  async function cancelTask(taskId) {
    if (!taskId) return
    try {
      await taskerApi.cancelTask(taskId)
      message.success('取消请求已提交')
      await refreshTask(taskId)
    } catch (error) {
      console.error(`取消任务 ${taskId} 失败`, error)
      message.error(error?.message || '取消任务失败')
    }
  }

  async function deleteTask(taskId) {
    if (!taskId) return
    try {
      await taskerApi.deleteTask(taskId)
      message.success('删除任务成功')
      // 从本地列表中移除
      const index = tasks.value.findIndex((item) => item.id === taskId)
      if (index >= 0) {
        tasks.value.splice(index, 1)
      }
    } catch (error) {
      console.error(`删除任务 ${taskId} 失败`, error)
      message.error(error?.message || '删除任务失败')
    }
  }

  function registerQueuedTask({ task_id, name, task_type, message: msg, payload } = {}) {
    if (!task_id) return
    const now = new Date().toISOString()
    upsertTask({
      id: task_id,
      name: name || '后台任务',
      type: task_type || 'manual',
      status: 'queued',
      progress: 0,
      message: msg || '任务已排队',
      created_at: now,
      updated_at: now,
      payload: payload || {}
    })
    syncPolling()
  }

  function openDrawer() {
    isDrawerOpen.value = true
    syncPolling()
  }

  function closeDrawer() {
    isDrawerOpen.value = false
    syncPolling()
  }

  function startPolling(interval = 5000) {
    if (pollingTimer) return
    pollingTimer = setInterval(() => {
      loadTasks()
    }, interval)
  }

  function stopPolling() {
    if (pollingTimer) {
      clearInterval(pollingTimer)
      pollingTimer = null
    }
  }

  // 轮询所有权收敛到 store：抽屉打开或存在活跃任务时持续轮询，否则停止，
  // 修复抽屉关闭后任务角标（activeCount）不再更新的问题。
  function syncPolling() {
    if (userStore.isAdmin && (isDrawerOpen.value || hasActiveTasks.value)) {
      startPolling()
    } else {
      stopPolling()
    }
  }

  function reset() {
    stopPolling()
    tasks.value = []
    lastError.value = null
    isDrawerOpen.value = false
    summary.value = createDefaultSummary()
  }

  return {
    isDrawerOpen,
    tasks,
    sortedTasks,
    totalCount,
    successCount,
    failedCount,
    loading,
    lastError,
    activeCount,
    loadTasks,
    refreshTask,
    cancelTask,
    deleteTask,
    registerQueuedTask,
    reset,
    openDrawer,
    closeDrawer
  }
})
