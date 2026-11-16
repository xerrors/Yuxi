import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { threadApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'
import { createThreadDraftStore } from '@/utils/thread_draft'

const PAGE_SIZE = 100
const threadDraftStore = createThreadDraftStore()

function countNonPinnedThreads(items) {
  let count = 0
  for (const item of items) {
    if (!item.is_pinned) count += 1
  }
  return count
}

export const useChatThreadsStore = defineStore('chatThreads', () => {
  const threads = ref([])
  const currentThreadId = ref(null)
  const threadCreationInFlight = ref(false)
  const hasMoreThreads = ref(true)
  const isLoadingMoreThreads = ref(false)

  const currentThread = computed(() => {
    if (!currentThreadId.value) return null
    return threads.value.find((thread) => thread.id === currentThreadId.value) || null
  })

  const setCurrentThreadId = (threadId, { force = false } = {}) => {
    if (threadCreationInFlight.value && !force) return false
    currentThreadId.value = threadId || null
    return true
  }

  const setThreadCreationInFlight = (value) => {
    threadCreationInFlight.value = Boolean(value)
  }

  const upsertThread = (thread) => {
    if (!thread?.id) return
    const index = threads.value.findIndex((item) => item.id === thread.id)
    if (index >= 0) {
      threads.value[index] = { ...threads.value[index], ...thread }
      return
    }
    threads.value = [thread, ...threads.value]
  }

  const setThreadStatus = (threadId, status) => {
    if (!threadId) return
    const index = threads.value.findIndex((item) => item.id === threadId)
    if (index >= 0) {
      threads.value[index] = { ...threads.value[index], thread_status: status }
    }
  }

  const markThreadViewed = async (threadId) => {
    if (!threadId) return
    try {
      const updatedThread = await threadApi.markThreadViewed(threadId)
      upsertThread(updatedThread)
      return updatedThread
    } catch (error) {
      console.warn(`Failed to mark thread viewed: ${threadId}`, error)
      return null
    }
  }

  const syncThreadStatuses = async (agentId = null) => {
    try {
      const fetchedThreads = await threadApi.getThreads(agentId, PAGE_SIZE, 0)
      if (!fetchedThreads) return
      const statusById = new Map(fetchedThreads.map((thread) => [thread.id, thread.thread_status]))
      threads.value = threads.value.map((thread) => {
        const latestStatus = statusById.get(thread.id)
        if (!latestStatus) return thread
        return { ...thread, thread_status: latestStatus }
      })
    } catch (error) {
      console.warn('Failed to sync thread statuses:', error)
    }
  }

  const loadThreads = async (agentId = null) => {
    try {
      const fetchedThreads = await threadApi.getThreads(agentId, PAGE_SIZE, 0)
      threads.value = fetchedThreads || []
      hasMoreThreads.value = Boolean(fetchedThreads && countNonPinnedThreads(fetchedThreads) >= PAGE_SIZE)
      if (
        currentThreadId.value &&
        !threads.value.find((thread) => thread.id === currentThreadId.value)
      ) {
        setCurrentThreadId(null)
      }
      return threads.value
    } catch (error) {
      console.error('Failed to fetch threads:', error)
      handleChatError(error, 'fetch')
      throw error
    }
  }

  const loadMoreThreads = async (agentId = null) => {
    if (isLoadingMoreThreads.value || !hasMoreThreads.value) return

    isLoadingMoreThreads.value = true
    try {
      const nonPinnedOffset = countNonPinnedThreads(threads.value)
      const fetchedThreads = await threadApi.getThreads(agentId, PAGE_SIZE, nonPinnedOffset)
      if (fetchedThreads && fetchedThreads.length > 0) {
        // 后端分页会重复返回置顶项，这里只追加列表中尚不存在的线程。
        const existingIds = new Set(threads.value.map((thread) => thread.id))
        const newThreads = fetchedThreads.filter((thread) => !existingIds.has(thread.id))
        threads.value = [...threads.value, ...newThreads]
        hasMoreThreads.value = countNonPinnedThreads(fetchedThreads) >= PAGE_SIZE
      } else {
        hasMoreThreads.value = false
      }
    } catch (error) {
      console.error('Failed to load more chats:', error)
      handleChatError(error, 'fetch')
    } finally {
      isLoadingMoreThreads.value = false
    }
  }

  const createThread = async (agentId, title = '新的对话', metadata = {}, options = {}) => {
    if (!agentId) return null

    try {
      const thread = await threadApi.createThread(agentId, title, metadata, options)
      if (thread) {
        threads.value = [thread, ...threads.value.filter((item) => item.id !== thread.id)]
      }
      return thread
    } catch (error) {
      console.error('Failed to create thread:', error)
      handleChatError(error, 'create')
      throw error
    }
  }

  const deleteThread = async (threadId) => {
    if (!threadId) return

    try {
      await threadApi.deleteThread(threadId)
      threads.value = threads.value.filter((thread) => thread.id !== threadId)
      // 线程已删除，同步清理其输入草稿
      threadDraftStore.remove(threadId)
      if (currentThreadId.value === threadId) {
        setCurrentThreadId(null)
      }
    } catch (error) {
      console.error('Failed to delete thread:', error)
      handleChatError(error, 'delete')
      throw error
    }
  }

  const removeThreadsByProject = (projectId) => {
    if (!projectId) return []
    const removedIds = []
    const remainingThreads = []
    for (const thread of threads.value) {
      if (thread.project_id !== projectId) {
        remainingThreads.push(thread)
        continue
      }
      removedIds.push(thread.id)
    }
    if (!removedIds.length) return []

    threads.value = remainingThreads
    removedIds.forEach((threadId) => threadDraftStore.remove(threadId))
    if (removedIds.includes(currentThreadId.value)) {
      setCurrentThreadId(null)
    }
    return removedIds
  }

  const updateThread = async (threadId, title, isPinned, toolApprovalMode) => {
    if (!threadId) return

    const normalizedTitle = title ? String(title).replace(/\s+/g, ' ').trim().slice(0, 255) : null
    if (title && !normalizedTitle) return
    if (!normalizedTitle && isPinned === undefined && toolApprovalMode === undefined) return

    try {
      const updatedThread = await threadApi.updateThread(
        threadId,
        normalizedTitle,
        isPinned,
        toolApprovalMode
      )
      upsertThread(updatedThread)
      return updatedThread
    } catch (error) {
      console.error('Failed to update thread:', error)
      handleChatError(error, 'update')
      throw error
    }
  }

  return {
    threads,
    currentThreadId,
    currentThread,
    threadCreationInFlight,
    hasMoreThreads,
    isLoadingMoreThreads,
    setCurrentThreadId,
    setThreadCreationInFlight,
    upsertThread,
    setThreadStatus,
    markThreadViewed,
    syncThreadStatuses,
    loadThreads,
    loadMoreThreads,
    createThread,
    deleteThread,
    removeThreadsByProject,
    updateThread
  }
})
