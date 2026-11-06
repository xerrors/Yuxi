import { computed, ref, watch } from 'vue'
import { useUserStore } from '@/modules/identity'
import { defineStore } from 'pinia'
import { threadApi } from '@/apis'
import { handleChatError } from '@/shared/lib/errorHandler'
import { threadDraftStore } from '@/modules/session/model/thread_draft'
import { useChatUIStore } from './chatUI'

const PAGE_SIZE = 100
export const useChatThreadsStore = defineStore('chatThreads', () => {
  const threads = ref([])
  const currentThreadId = ref(null)
  const hasLoadedThreads = ref(false)
  const threadsLoadError = ref('')
  const threadCreationInFlight = ref(false)
  const hasMoreThreads = ref(true)
  const isLoadingMoreThreads = ref(false)
  const userStore = useUserStore()
  let after = null
  let threadListOperation = Promise.resolve()
  let threadListGeneration = 0
  // 切换账号重置列表状态，避免旧请求覆盖新账号的导航。
  watch(
    () => userStore.userId,
    () => {
      threadListGeneration += 1
      threads.value = []
      currentThreadId.value = null
      hasLoadedThreads.value = false
      threadsLoadError.value = ''
      hasMoreThreads.value = true
      isLoadingMoreThreads.value = false
      after = null
    },
    { flush: 'sync' }
  )
  /** 列表读取与写操作串行，保持当前页和选中会话一致。 */
  const withThreadListUpdate = (operation) => {
    const next = threadListOperation.catch(() => {}).then(operation)
    threadListOperation = next
    return next
  }
  const currentThread = computed(() => {
    if (!currentThreadId.value) return null
    return threads.value.find((thread) => thread.id === currentThreadId.value) || null
  })

  const setCurrentThreadId = (threadId, { force = false } = {}) => {
    if (threadCreationInFlight.value && !force) return false
    currentThreadId.value = threadId || null
    useChatUIStore().lastThreadId = currentThreadId.value
    return true
  }

  const setThreadCreationInFlight = (value) => {
    threadCreationInFlight.value = Boolean(value)
  }

  /**
   * 合并线程到列表。归档线程不作为新条目进入侧栏，但当前正在打开的线程仍需保留
   * 详情（URL 直达归档会话时用于只读提示）；侧栏渲染负责隐藏归档项。
   */
  const upsertThread = (thread) => {
    if (!thread?.id) return
    const index = threads.value.findIndex((item) => item.id === thread.id)
    const archived = thread.yuxi?.archived === true
    if (index >= 0) {
      threads.value[index] = {
        ...threads.value[index],
        ...thread,
        yuxi: { ...threads.value[index].yuxi, ...thread.yuxi }
      }
      return
    }
    if (archived && thread.id !== currentThreadId.value) return
    threads.value = [thread, ...threads.value]
  }

  const markThreadViewed = async (threadId) => {
    if (!threadId) return
    const currentGeneration = threadListGeneration
    try {
      const updatedThread = await threadApi.markThreadViewed(threadId)
      if (currentGeneration !== threadListGeneration) return null
      upsertThread(updatedThread)
      return updatedThread
    } catch (error) {
      if (currentGeneration !== threadListGeneration) return null
      console.warn(`Failed to mark thread viewed: ${threadId}`, error)
      return null
    }
  }

  const syncThreadStatuses = async (agentId = null) => {
    const currentGeneration = threadListGeneration
    return withThreadListUpdate(async () => {
      if (currentGeneration !== threadListGeneration) return
      try {
        const [page, pinnedThreads] = await Promise.all([
          threadApi.getThreads(agentId, PAGE_SIZE),
          fetchPinnedThreads(agentId)
        ])
        if (currentGeneration !== threadListGeneration) return
        const statusById = new Map(
          [...page.data, ...pinnedThreads].map((thread) => [thread.id, thread])
        )
        threads.value = threads.value.map((thread) => {
          const latestStatus = statusById.get(thread.id)
          if (!latestStatus) return thread
          return {
            ...thread,
            status: latestStatus.status,
            yuxi: { ...thread.yuxi, ...latestStatus.yuxi }
          }
        })
      } catch (error) {
        if (currentGeneration !== threadListGeneration) return
        console.warn('Failed to sync thread statuses:', error)
      }
    })
  }

  const loadThreads = async (agentId = null) => {
    const currentGeneration = threadListGeneration
    return withThreadListUpdate(async () => {
      if (currentGeneration !== threadListGeneration) return
      threadsLoadError.value = ''
      try {
        const [fetchedThreads, pinnedThreads] = await Promise.all([
          threadApi.getThreads(agentId, PAGE_SIZE),
          fetchPinnedThreads(agentId)
        ])
        if (currentGeneration !== threadListGeneration) return
        const loaded = new Map(
          [...fetchedThreads.data, ...pinnedThreads].map((thread) => [thread.id, thread])
        )
        const cachedDetails = threads.value.filter(
          (thread) =>
            !loaded.has(thread.id) &&
            (thread.yuxi.parent_session_id || thread.id === currentThreadId.value)
        )
        threads.value = [...loaded.values(), ...cachedDetails]
        after = fetchedThreads.last_id
        hasMoreThreads.value = fetchedThreads.has_more
        hasLoadedThreads.value = true
        return threads.value
      } catch (error) {
        if (currentGeneration !== threadListGeneration) return
        if (!hasLoadedThreads.value) threadsLoadError.value = '对话加载失败'
        console.error('Failed to fetch threads:', error)
        handleChatError(error, 'fetch')
        throw error
      }
    })
  }

  const loadMoreThreads = async (agentId = null) => {
    if (isLoadingMoreThreads.value || !hasMoreThreads.value) return

    const currentGeneration = threadListGeneration
    isLoadingMoreThreads.value = true
    return withThreadListUpdate(async () => {
      try {
        if (currentGeneration !== threadListGeneration) return
        const page = await threadApi.getThreads(agentId, PAGE_SIZE, after)
        if (currentGeneration !== threadListGeneration) return
        const existingIds = new Set(threads.value.map((thread) => thread.id))
        threads.value.push(...page.data.filter((thread) => !existingIds.has(thread.id)))
        after = page.last_id
        hasMoreThreads.value = page.has_more
      } catch (error) {
        if (currentGeneration !== threadListGeneration) return
        console.error('Failed to load more chats:', error)
        handleChatError(error, 'fetch')
      } finally {
        if (currentGeneration === threadListGeneration) isLoadingMoreThreads.value = false
      }
    })
  }

  /** 置顶独立分页读取，较老的置顶会话也始终出现在侧边栏。 */
  const fetchPinnedThreads = async (agentId) => {
    const pinned = []
    let cursor = null
    let page
    do {
      page = await threadApi.getThreads(agentId, PAGE_SIZE, cursor, { isPinned: true })
      pinned.push(...page.data)
      cursor = page.last_id
    } while (page.has_more)
    return pinned
  }

  const createThread = async (agentId, title = null, metadata = {}, options = {}) => {
    const currentGeneration = threadListGeneration
    return withThreadListUpdate(async () => {
      if (!agentId || currentGeneration !== threadListGeneration) return null

      try {
        const thread = await threadApi.createThread(agentId, title, metadata, options)
        if (currentGeneration !== threadListGeneration) return null
        if (thread) {
          threads.value = [thread, ...threads.value.filter((item) => item.id !== thread.id)]
        }
        return thread
      } catch (error) {
        if (currentGeneration !== threadListGeneration) return null
        console.error('Failed to create thread:', error)
        handleChatError(error, 'create')
        throw error
      }
    })
  }

  const archiveThread = (threadId) => {
    const currentGeneration = threadListGeneration
    return withThreadListUpdate(async () => {
      if (!threadId || currentGeneration !== threadListGeneration) return
      try {
        await threadApi.archiveThread(threadId)
        if (currentGeneration !== threadListGeneration) return
        threads.value = threads.value.filter((thread) => thread.id !== threadId)
        // 归档后清理当前线程的本地草稿。
        threadDraftStore.remove(threadId)
        if (currentThreadId.value === threadId) setCurrentThreadId(null)
        if (useChatUIStore().lastThreadId === threadId) useChatUIStore().lastThreadId = null
      } catch (error) {
        if (currentGeneration !== threadListGeneration) return
        console.error('Failed to archive thread:', error)
        handleChatError(error, 'archive')
        throw error
      }
    })
  }

  const removeThreadsByProject = (projectId) => {
    if (!projectId) return []
    const removedIds = []
    const remainingThreads = []
    for (const thread of threads.value) {
      if (thread.yuxi.project_id !== projectId) {
        remainingThreads.push(thread)
        continue
      }
      removedIds.push(thread.id)
    }
    if (!removedIds.length) return []

    threads.value = remainingThreads
    removedIds.forEach((threadId) => threadDraftStore.remove(threadId))
    if (removedIds.includes(currentThreadId.value)) setCurrentThreadId(null)
    if (removedIds.includes(useChatUIStore().lastThreadId)) useChatUIStore().lastThreadId = null
    return removedIds
  }

  const updateThread = async (threadId, title, isPinned, toolApprovalMode, modelSpec) => {
    const currentGeneration = threadListGeneration
    return withThreadListUpdate(async () => {
      if (!threadId || currentGeneration !== threadListGeneration) return

      const normalizedTitle = title ? String(title).replace(/\s+/g, ' ').trim().slice(0, 255) : null
      if (title && !normalizedTitle) return
      if (
        !normalizedTitle &&
        isPinned === undefined &&
        toolApprovalMode === undefined &&
        modelSpec === undefined
      )
        return

      try {
        const updatedThread = await threadApi.updateThread(
          threadId,
          normalizedTitle,
          isPinned,
          toolApprovalMode,
          modelSpec
        )
        if (currentGeneration !== threadListGeneration) return
        upsertThread(updatedThread)
        return updatedThread
      } catch (error) {
        if (currentGeneration !== threadListGeneration) return
        console.error('Failed to update thread:', error)
        handleChatError(error, 'update')
        throw error
      }
    })
  }

  return {
    threads,
    currentThreadId,
    currentThread,
    hasLoadedThreads,
    threadsLoadError,
    threadCreationInFlight,
    hasMoreThreads,
    isLoadingMoreThreads,
    setCurrentThreadId,
    setThreadCreationInFlight,
    upsertThread,
    markThreadViewed,
    syncThreadStatuses,
    withThreadListUpdate,
    loadThreads,
    loadMoreThreads,
    createThread,
    archiveThread,
    removeThreadsByProject,
    updateThread
  }
})
