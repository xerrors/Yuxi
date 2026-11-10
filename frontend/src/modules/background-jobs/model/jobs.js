import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { backgroundJobsApi } from '@/apis/background_jobs'
import { useUserStore } from '@/modules/identity'
import { parseToShanghai } from '@/shared/lib/time'

const ACTIVE_STATUSES = new Set(['pending', 'running'])
// 失败计数只认 failed。cancelled 仍是终态，留在「全部」里显示为已取消。
const FAILED_STATUSES = new Set(['failed'])
const TERMINAL_STATUSES = new Set(['success', 'failed', 'cancelled'])

const createDefaultSummary = () => ({
  total: 0,
  filtered_total: 0,
  status_counts: {},
  type_counts: {}
})

const toJob = (raw = {}) => ({
  id: raw.id,
  name: raw.name || '后台作业',
  type: raw.type || 'general',
  status: raw.status || 'pending',
  progress: raw.progress ?? 0,
  message: raw.message || '',
  created_at: raw.created_at,
  updated_at: raw.updated_at,
  started_at: raw.started_at,
  completed_at: raw.completed_at,
  result: raw.result,
  error: raw.error,
  cancel_requested: raw.cancel_requested || false
})

export const useBackgroundJobsStore = defineStore('backgroundJobs', () => {
  const userStore = useUserStore()
  const jobs = ref([])
  const loading = ref(false)
  const lastError = ref(null)
  const isDrawerOpen = ref(false)
  const summary = ref(createDefaultSummary())
  let pollingTimer = null
  let sessionGeneration = 0
  let listRequestId = 0
  let jobRevision = 0
  let pollingFailures = 0
  const detailRequests = new Map()
  const pendingReceipts = new Set()
  const completionCallbacks = new Map()

  const sortedJobs = computed(() => {
    return [...jobs.value].sort((a, b) => {
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

  // 是否存在需要持续轮询的作业：summary 统计或详情快照中的活跃作业
  const hasActiveJobs = computed(
    () => activeCount.value > 0 || jobs.value.some((job) => ACTIVE_STATUSES.has(job.status))
  )

  /** 作业终态只通知一次，页面重新读取自己的业务资源。 */
  function notifyCompletion(job) {
    if (!TERMINAL_STATUSES.has(job.status)) return
    const callback = completionCallbacks.get(job.id)
    completionCallbacks.delete(job.id)
    callback?.()
  }

  function upsertJob(rawJob) {
    if (!rawJob || !rawJob.id) return
    jobRevision += 1
    const job = toJob(rawJob)
    const index = jobs.value.findIndex((item) => item.id === job.id)
    if (index >= 0) {
      jobs.value.splice(index, 1, { ...jobs.value[index], ...job })
    } else {
      jobs.value.unshift(job)
    }
    notifyCompletion(job)
  }

  async function loadJobs(params = {}) {
    if (!userStore.isAdmin) {
      reset()
      return
    }

    const requestId = ++listRequestId
    const revision = jobRevision
    stopPolling()
    loading.value = true
    lastError.value = null
    try {
      const response = await backgroundJobsApi.fetchJobs(params)
      if (requestId !== listRequestId || revision !== jobRevision) return
      const jobList = response?.jobs || []
      summary.value = {
        ...createDefaultSummary(),
        ...(response?.summary || {})
      }
      jobs.value = jobList.map(toJob)
      pendingReceipts.clear()
      pollingFailures = 0
      jobs.value.forEach(notifyCompletion)
      // 已登记作业可能被列表分页或筛选排除，详情读取仍由同一轮询拥有。
      const listedIds = new Set(jobs.value.map((job) => job.id))
      await Promise.all(
        [...completionCallbacks.keys()].filter((id) => !listedIds.has(id)).map(refreshJob)
      )
    } catch (error) {
      if (requestId !== listRequestId || revision !== jobRevision) return
      console.error('加载作业列表失败', error)
      lastError.value = error
      pollingFailures += 1
    } finally {
      if (requestId === listRequestId) {
        loading.value = false
        syncPolling()
      }
    }
  }

  async function refreshJob(jobId) {
    if (!jobId) return
    const request = Symbol(jobId)
    const listId = listRequestId
    detailRequests.set(jobId, request)
    try {
      const response = await backgroundJobsApi.fetchJobDetail(jobId)
      if (detailRequests.get(jobId) !== request || listId !== listRequestId) return
      if (response?.job) {
        upsertJob(response.job)
        pendingReceipts.delete(jobId)
      }
    } catch (error) {
      if (detailRequests.get(jobId) !== request || listId !== listRequestId) return
      console.error(`刷新作业 ${jobId} 详情失败`, error)
      lastError.value = error
    } finally {
      if (detailRequests.get(jobId) === request) detailRequests.delete(jobId)
    }
  }

  async function cancelJob(jobId) {
    if (!jobId) return
    const generation = sessionGeneration
    try {
      await backgroundJobsApi.cancelJob(jobId)
      if (generation !== sessionGeneration) return
      message.success('取消请求已提交')
      await refreshJob(jobId)
    } catch (error) {
      if (generation !== sessionGeneration) return
      console.error(`取消作业 ${jobId} 失败`, error)
      message.error(error?.message || '取消作业失败')
    }
  }

  async function deleteJob(jobId) {
    if (!jobId) return
    const generation = sessionGeneration
    try {
      await backgroundJobsApi.deleteJob(jobId)
      if (generation !== sessionGeneration) return
      jobRevision += 1
      detailRequests.delete(jobId)
      completionCallbacks.delete(jobId)
      message.success('删除作业成功')
      // 从本地列表中移除
      const index = jobs.value.findIndex((item) => item.id === jobId)
      if (index >= 0) {
        jobs.value.splice(index, 1)
      }
    } catch (error) {
      if (generation !== sessionGeneration) return
      console.error(`删除作业 ${jobId} 失败`, error)
      message.error(error?.message || '删除作业失败')
    }
  }

  function registerJobReceipt({ job_id } = {}) {
    if (!job_id) return
    pendingReceipts.add(job_id)
    jobRevision += 1
    return refreshJob(job_id).then(syncPolling)
  }

  /** 在提交开始时绑定会话，拒绝切换账号后晚到的入队回执。 */
  function createJobRegistration(onSettled) {
    const generation = sessionGeneration
    return (job) => {
      if (generation !== sessionGeneration || !userStore.isAdmin) return
      if (job?.job_id && onSettled) completionCallbacks.set(job.job_id, onSettled)
      return registerJobReceipt(job)
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
      void loadJobs()
    }, interval)
  }

  function stopPolling() {
    if (pollingTimer) {
      clearTimeout(pollingTimer)
      pollingTimer = null
    }
  }

  // 轮询所有权收敛到 store：抽屉打开或存在活跃作业时持续轮询，否则停止，
  // 修复抽屉关闭后作业角标（activeCount）不再更新的问题。
  function syncPolling() {
    if (
      userStore.isAdmin &&
      (isDrawerOpen.value ||
        hasActiveJobs.value ||
        pendingReceipts.size ||
        completionCallbacks.size)
    ) {
      startPolling()
    } else {
      stopPolling()
    }
  }

  function reset() {
    sessionGeneration += 1
    listRequestId += 1
    detailRequests.clear()
    pendingReceipts.clear()
    completionCallbacks.clear()
    pollingFailures = 0
    loading.value = false
    stopPolling()
    jobs.value = []
    lastError.value = null
    isDrawerOpen.value = false
    summary.value = createDefaultSummary()
  }

  // 同步失效：退出或切换账号后，旧请求即使完成也不能写回新会话。
  watch([() => userStore.token, () => userStore.userRole], reset, { flush: 'sync' })
  onScopeDispose(reset)

  return {
    isDrawerOpen,
    jobs,
    sortedJobs,
    totalCount,
    successCount,
    failedCount,
    loading,
    lastError,
    activeCount,
    loadJobs,
    refreshJob,
    cancelJob,
    deleteJob,
    createJobRegistration,
    reset,
    openDrawer,
    closeDrawer
  }
})
