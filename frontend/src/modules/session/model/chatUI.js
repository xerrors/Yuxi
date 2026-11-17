import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { useUserStore } from '@/modules/identity'

export const useChatUIStore = defineStore(
  'chatUI',
  () => {
    // ==================== 聊天界面 UI 状态 ====================
    // 应用侧边栏折叠态
    const sidebarCollapsed = ref(false)
    const navigationByUser = ref({})
    const userStore = useUserStore()
    const navigation = computed(() => navigationByUser.value?.[String(userStore.userId)] || {})
    const lastThreadId = computed({
      get: () =>
        userStore.userId != null && typeof navigation.value.lastThreadId === 'string'
          ? navigation.value.lastThreadId || null
          : null,
      set: (threadId) => updateNavigation({ lastThreadId: threadId || null })
    })
    const sessionsPinned = computed({
      get: () => userStore.userId != null && navigation.value.sessionsPinned === true,
      set: (pinned) => updateNavigation({ sessionsPinned: pinned })
    })
    const sessionPeekOpen = ref(false)
    const projectsExpanded = ref(true)
    const recentExpanded = ref(true)
    const expandedProjects = ref(new Set())
    const projectVisibleCounts = ref({})
    const navigationScrollTop = ref(0)
    const projectPendingId = ref(null)
    watch(
      () => userStore.userId,
      () => {
        sessionPeekOpen.value = false
        projectPendingId.value = null
        projectsExpanded.value = true
        recentExpanded.value = true
        expandedProjects.value = new Set()
        projectVisibleCounts.value = {}
        navigationScrollTop.value = 0
      },
      { flush: 'sync' }
    )

    /** 按已认证账号保存导航偏好，不复用其他账号的线程或展开状态。 */
    function updateNavigation(patch) {
      if (userStore.userId == null) return
      navigationByUser.value = {
        ...navigationByUser.value,
        [String(userStore.userId)]: {
          lastThreadId: lastThreadId.value,
          sessionsPinned: sessionsPinned.value,
          ...patch
        }
      }
    }

    return {
      // 状态
      sidebarCollapsed,
      navigationByUser,
      lastThreadId,
      sessionsPinned,
      sessionPeekOpen,
      projectsExpanded,
      recentExpanded,
      expandedProjects,
      projectVisibleCounts,
      navigationScrollTop,
      projectPendingId
    }
  },
  {
    persist: {
      key: 'chat-ui-store',
      storage: typeof localStorage === 'undefined' ? undefined : localStorage,
      pick: ['sidebarCollapsed', 'navigationByUser'],
      debug: true
    }
  }
)
