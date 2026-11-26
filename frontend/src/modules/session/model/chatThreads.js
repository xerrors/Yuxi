import { ref, watch } from 'vue'
import { useUserStore } from '@/modules/identity'
import { defineStore } from 'pinia'
import { threadApi } from '@/apis'
import { handleChatError } from '@/shared/lib/errorHandler'
import { threadDraftStore } from '@/modules/session/model/thread_draft'
import { useChatUIStore } from './chatUI'

const PAGE_SIZE = 100
export const useChatThreadsStore = defineStore('chatThreads', () => {
  const threads = ref([])
  const hasLoadedThreads = ref(false)
  const threadsLoadError = ref('')
  const threadCreationInFlight = ref(false)
  const hasMoreThreads = ref(true)
  const isLoadingMoreThreads = ref(false)
  const userStore = useUserStore()
  let after = null
  let threadListOperation = Promise.resolve()
  let threadListGeneration = 0
  let sidebarPages = new Map()
  // 切换账号重置列表状态，避免旧请求覆盖新账号的导航。
  watch(
    [() => userStore.token, () => userStore.userId],
    () => {
      threadListGeneration += 1
      sidebarPages = new Map()
      threads.value = []
      threadCreationInFlight.value = false
      hasLoadedThreads.value = false
      threadsLoadError.value = ''
      hasMoreThreads.value = true
      isLoadingMoreThreads.value = false
      after = null
    },
    { flush: 'sync' }
  )
  /** 列表读取与写操作串行，保持当前页的一致性。 */
  const withThreadListUpdate = (operation) => {
    const next = threadListOperation.catch(() => {}).then(operation)
    threadListOperation = next
    return next
  }
  /** 新建期间暂停导航，旧身份完成请求后只释放自己的占用。 */
  const beginThreadCreation = () => {
    const currentGeneration = threadListGeneration
    threadCreationInFlight.value = true
    return () => {
      if (currentGeneration === threadListGeneration) threadCreationInFlight.value = false
    }
  }

  /** 更新当前列表中的摘要，详情和协作成员读取不插入条目。 */
  const updateThreadSummary = (threadId, summary) => {
    const index = threads.value.findIndex((item) => item.id === threadId)
    if (index === -1 || !summary) return
    if (summary.yuxi?.archived) {
      threads.value.splice(index, 1)
      return
    }
    const previous = threads.value[index]
    threads.value[index] = { ...previous, ...summary, yuxi: { ...previous.yuxi, ...summary.yuxi } }
  }

  const markThreadViewed = async (threadId) => {
    if (!threadId) return
    const currentGeneration = threadListGeneration
    try {
      const updatedThread = await threadApi.markThreadViewed(threadId)
      if (currentGeneration !== threadListGeneration || !updatedThread?.yuxi) return null
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
        const [page, pinnedThreads] = await fetchSidebarPages(agentId)
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
        const [fetchedThreads, pinnedThreads] = await fetchSidebarPages(agentId)
        if (currentGeneration !== threadListGeneration) return
        const loaded = new Map(
          [...fetchedThreads.data, ...pinnedThreads].map((thread) => [thread.id, thread])
        )
        threads.value = [...loaded.values()]
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

  /** 每次成功刷新只保留当前普通首页及遍历过的置顶分页。 */
  const fetchSidebarPages = async (agentId) => {
    const pageCache = { previous: sidebarPages, next: new Map() }
    const [page, firstPinned] = await Promise.all([
      threadApi.getThreads(agentId, PAGE_SIZE, undefined, { pageCache }),
      threadApi.getThreads(agentId, PAGE_SIZE, null, { isPinned: true, pageCache })
    ])
    const pinned = [...firstPinned.data]
    let pinnedPage = firstPinned
    while (pinnedPage.has_more && sidebarPages === pageCache.previous) {
      pinnedPage = await threadApi.getThreads(agentId, PAGE_SIZE, pinnedPage.last_id, {
        isPinned: true,
        pageCache
      })
      pinned.push(...pinnedPage.data)
    }
    if (sidebarPages === pageCache.previous) sidebarPages = pageCache.next
    return [page, pinned]
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
        const archived = await threadApi.archiveThread(threadId)
        if (currentGeneration !== threadListGeneration) return
        threads.value = threads.value.filter((thread) => thread.id !== threadId)
        // 归档后清理当前线程的本地草稿。
        threadDraftStore.remove(threadId)
        if (useChatUIStore().lastThreadId === threadId) useChatUIStore().lastThreadId = null
        return archived
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
        updateThreadSummary(threadId, {
          yuxi: { title: updatedThread.yuxi.title, is_pinned: updatedThread.yuxi.is_pinned }
        })
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
    hasLoadedThreads,
    threadsLoadError,
    threadCreationInFlight,
    hasMoreThreads,
    isLoadingMoreThreads,
    beginThreadCreation,
    updateThreadSummary,
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
