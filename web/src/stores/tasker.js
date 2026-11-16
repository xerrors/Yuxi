import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref, watch } from 'vue'
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
  let sessionGeneration = 0
  let listRequestId = 0
  let taskRevision = 0
  let pollingFailures = 0
  const detailRequests = new Map()

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
    taskRevision += 1
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
      reset()
      return
    }

    const requestId = ++listRequestId
    const revision = taskRevision
    stopPolling()
    loading.value = true
    lastError.value = null
    try {
      const response = await taskerApi.fetchTasks(params)
      if (requestId !== listRequestId || revision !== taskRevision) return
      const taskList = response?.tasks || []
      summary.value = {
        ...createDefaultSummary(),
        ...(response?.summary || {})
      }
      tasks.value = taskList.map(toTask)
      pollingFailures = 0
    } catch (error) {
      if (requestId !== listRequestId || revision !== taskRevision) return
      console.error('加载任务列表失败', error)
      lastError.value = error
      pollingFailures += 1
    } finally {
      if (requestId === listRequestId) {
        loading.value = false
        syncPolling()
      }
    }
  }

  async function refreshTask(taskId) {
    if (!taskId) return
    const request = Symbol(taskId)
    const listId = listRequestId
    detailRequests.set(taskId, request)
    try {
      const response = await taskerApi.fetchTaskDetail(taskId)
      if (detailRequests.get(taskId) !== request || listId !== listRequestId) return
      if (response?.task) {
        upsertTask(response.task)
      }
    } catch (error) {
      if (detailRequests.get(taskId) !== request || listId !== listRequestId) return
      console.error(`刷新任务 ${taskId} 详情失败`, error)
      lastError.value = error
    } finally {
      if (detailRequests.get(taskId) === request) detailRequests.delete(taskId)
    }
  }

  async function cancelTask(taskId) {
    if (!taskId) return
    const generation = sessionGeneration
    try {
      await taskerApi.cancelTask(taskId)
      if (generation !== sessionGeneration) return
      message.success('取消请求已提交')
      await refreshTask(taskId)
    } catch (error) {
      if (generation !== sessionGeneration) return
      console.error(`取消任务 ${taskId} 失败`, error)
      message.error(error?.message || '取消任务失败')
    }
  }

  async function deleteTask(taskId) {
    if (!taskId) return
    const generation = sessionGeneration
    try {
      await taskerApi.deleteTask(taskId)
      if (generation !== sessionGeneration) return
      taskRevision += 1
      detailRequests.delete(taskId)
      message.success('删除任务成功')
      // 从本地列表中移除
      const index = tasks.value.findIndex((item) => item.id === taskId)
      if (index >= 0) {
        tasks.value.splice(index, 1)
      }
    } catch (error) {
      if (generation !== sessionGeneration) return
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

  /** 在提交开始时绑定会话，拒绝切换账号后晚到的入队回执。 */
  function createTaskRegistration() {
    const generation = sessionGeneration
    return (task) => {
      if (generation !== sessionGeneration || !userStore.isAdmin) return
      registerQueuedTask(task)
    }
  }

  function openDrawer() {
    isDrawerOpen.value = true
    syncPolling()
  }

  function closeDrawer() {
    isDrawerOpen.value = false
    syncPolling()
  }

  function startPolling() {
    if (pollingTimer || loading.value) return
    const interval = Math.min(5000 * 2 ** Math.min(pollingFailures, 3), 30000)
    pollingTimer = setTimeout(() => {
      pollingTimer = null
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        syncPolling()
        return
      }
      void loadTasks()
    }, interval)
  }

  function stopPolling() {
    if (pollingTimer) {
      clearTimeout(pollingTimer)
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
    sessionGeneration += 1
    listRequestId += 1
    detailRequests.clear()
    pollingFailures = 0
    loading.value = false
    stopPolling()
    tasks.value = []
    lastError.value = null
    isDrawerOpen.value = false
    summary.value = createDefaultSummary()
  }

  // 同步失效：退出或切换账号后，旧请求即使完成也不能写回新会话。
  watch([() => userStore.token, () => userStore.userRole], reset, { flush: 'sync' })
  onScopeDispose(reset)

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
    createTaskRegistration,
    reset,
    openDrawer,
    closeDrawer
  }
})
