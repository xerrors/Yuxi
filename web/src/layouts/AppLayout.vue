<script setup>
import { ref, onMounted, onUnmounted, computed, provide, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  BarChart3,
  ClipboardList,
  LibraryBig,
  Box,
  FolderKanban,
  PanelLeft,
  PanelLeftOpen,
  PanelRight,
  MessageCirclePlus,
  Maximize2,
  PictureInPicture2,
  Search,
  X
} from 'lucide-vue-next'

import { useConfigStore } from '@/stores/config'
import { useAgentStore } from '@/stores/agent'
import { useChatThreadsStore } from '@/stores/chatThreads'
import { useChatUIStore } from '@/stores/chatUI'
import { useDatabaseStore } from '@/stores/database'
import { useInfoStore } from '@/stores/info'
import { useRuntimeCapabilitiesStore } from '@/stores/runtimeCapabilities'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import {
  resolveAppNavigationPath,
  shouldShowAppSidebar,
  useEmbedContext
} from '@/composables/useEmbedMode'
import { useOAEmbedBridge } from '@/composables/useOAEmbedBridge'
import { storeToRefs } from 'pinia'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import DebugComponent from '@/components/DebugComponent.vue'
import TaskCenterDrawer from '@/components/TaskCenterDrawer.vue'
import ConversationNavSection from '@/components/ConversationNavSection.vue'
import GlobalSearchModal from '@/components/GlobalSearchModal.vue'
import { searchWorkspaceFiles } from '@/apis/workspace_api'
import { SETTINGS_ROUTES } from '@/utils/settingsNavigation'

const configStore = useConfigStore()
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const chatUIStore = useChatUIStore()
const databaseStore = useDatabaseStore()
const infoStore = useInfoStore()
const runtimeCapabilitiesStore = useRuntimeCapabilitiesStore()
const taskerStore = useTaskerStore()
const userStore = useUserStore()
const route = useRoute()
const router = useRouter()
const {
  isEmbedded,
  displayMode: embedDisplayMode,
  modeConfirmed: embedModeConfirmed
} = useEmbedContext()
const oaEmbedBridge = useOAEmbedBridge(isEmbedded)
const {
  isAuthorized: embedAuthorized,
  statusMessage: embedStatusMessage,
  requestDisplayMode,
  requestClose
} = oaEmbedBridge
provide('oaEmbedBridge', oaEmbedBridge)
const { activeCount: activeCountRef, isDrawerOpen } = storeToRefs(taskerStore)
const { knowledgeEnabled } = storeToRefs(runtimeCapabilitiesStore)
const { threads, currentThreadId, hasMoreThreads, isLoadingMoreThreads } =
  storeToRefs(chatThreadsStore)
const conversationRouteNames = new Set([
  'AgentComp',
  'AgentCompWithThreadId',
  'EmbedAgent',
  'EmbedAgentWithThreadId'
])
const embedModeOptions = [
  { value: 'fixed', label: '固定模式', icon: PanelRight },
  { value: 'floating', label: '浮窗模式', icon: PictureInPicture2 },
  { value: 'fullscreen', label: '全屏模式', icon: Maximize2 }
]

// Add state for debug modal
const showDebugModal = ref(false)

const { sidebarCollapsed } = storeToRefs(chatUIStore)
const embedSidebarCollapsed = ref(false)
const isSettingsRoute = computed(() => Boolean(route.meta.settingsTab))
// 设置页已经提供专属导航，隐藏应用主侧栏，避免全屏嵌入时出现双侧栏。
const showSidebar = computed(
  () => !isSettingsRoute.value && shouldShowAppSidebar(isEmbedded.value, embedDisplayMode.value)
)
const layoutSidebarCollapsed = computed(() =>
  isEmbedded.value ? embedSidebarCollapsed.value : sidebarCollapsed.value
)
const conversationSearchOpen = ref(false)
const canUseAgents = computed(() => userStore.hasPermission('agent:use'))
const searchModes = computed(() => (canUseAgents.value ? ['conversation', 'file'] : ['file']))
const canAccessExtensions = computed(() =>
  [
    'knowledge_base:read',
    'knowledge_base:manage',
    'skill:use',
    'skill:manage',
    'tool:manage',
    'mcp:manage'
  ].some((permission) => userStore.hasPermission(permission))
)

// Provide settings modal methods to child components
const openSettingsModal = (tab) => {
  const tabId = tab || (userStore.hasPermission('system_config:manage') ? 'base' : 'account')
  const target = SETTINGS_ROUTES.find((item) => item.id === tabId)
  if (!target) return

  router.push({ path: target.path, query: { returnTo: route.fullPath } })
}

// Handle debug modal close
const handleDebugModalClose = () => {
  showDebugModal.value = false
}

const getRemoteConfig = async () => {
  try {
    await configStore.refreshConfig()
  } catch (error) {
    console.warn('加载系统配置失败:', error)
  }
}

const getRemoteDatabase = async () => {
  await runtimeCapabilitiesStore.ensureLoaded()
  if (!knowledgeEnabled.value) return
  try {
    await databaseStore.loadDatabases()
  } catch (error) {
    console.warn('加载知识库列表失败:', error)
  }
}

let layoutInitialization = null

const initializeLayout = () => {
  if (layoutInitialization) return layoutInitialization

  layoutInitialization = (async () => {
    // 加载信息配置与知识库数据无依赖，可并行
    const databaseRequest = userStore.hasPermission('knowledge_base:read')
      ? getRemoteDatabase()
      : null
    await Promise.all([infoStore.loadInfoConfig(), databaseRequest])
    if (canUseAgents.value) await initAgentNavigation()
    if (userStore.hasPermission('system_config:manage')) await getRemoteConfig()
    // 仅管理员加载任务中心数据
    if (userStore.hasPermission('system_task:manage')) {
      taskerStore.loadTasks()
    }
  })()

  return layoutInitialization
}

const initializeLayoutWhenReady = () => {
  if (!isEmbedded.value || (showSidebar.value && userStore.userId)) {
    void initializeLayout()
  }
}

onMounted(() => {
  initializeLayoutWhenReady()
  startThreadStatusSync()
})

// 低频刷新侧边栏线程状态，让后台线程完成时也能从 loading 转为 ready/done。
const THREAD_STATUS_SYNC_INTERVAL_MS = 12 * 1000
let threadStatusSyncTimer = null

const startThreadStatusSync = () => {
  if (threadStatusSyncTimer) return
  threadStatusSyncTimer = setInterval(() => {
    if (
      !canUseAgents.value ||
      layoutSidebarCollapsed.value ||
      (typeof document !== 'undefined' && document.visibilityState !== 'visible')
    ) {
      return
    }
    void chatThreadsStore.syncThreadStatuses()
  }, THREAD_STATUS_SYNC_INTERVAL_MS)
}

onUnmounted(() => {
  if (threadStatusSyncTimer) {
    clearInterval(threadStatusSyncTimer)
    threadStatusSyncTimer = null
  }
})
watch([showSidebar, () => userStore.userId], initializeLayoutWhenReady)

const activeTaskCount = computed(() => activeCountRef.value || 0)
const isConversationRoute = computed(() => conversationRouteNames.has(route.name))
const activeConversationThreadId = computed(() =>
  isConversationRoute.value ? currentThreadId.value : null
)
const organizationName = computed(() => {
  return infoStore.organization.name || infoStore.branding.name || 'Yuxi'
})

// 下面是导航菜单部分，添加智能体项
const mainList = computed(() => {
  const items = []

  if (canUseAgents.value) {
    items.push({
      name: '新建对话',
      path: resolveAppNavigationPath(isEmbedded.value, '/agent'),
      icon: MessageCirclePlus,
      activeIcon: MessageCirclePlus,
      action: true,
      exactActive: true
    })
  }

  if (
    ['agent:use', 'agent:manage', 'model_provider:manage'].some((permission) =>
      userStore.hasPermission(permission)
    )
  ) {
    items.push({
      name: '智能体',
      path: resolveAppNavigationPath(isEmbedded.value, '/agent-manage'),
      icon: Box,
      activeIcon: Box
    })
  }

  items.push({
    name: '工作区',
    path: resolveAppNavigationPath(isEmbedded.value, '/workspace'),
    icon: FolderKanban,
    activeIcon: FolderKanban
  })

  if (canAccessExtensions.value) {
    items.push({
      name: knowledgeEnabled.value ? '知识库 · 技能' : '技能',
      path: resolveAppNavigationPath(isEmbedded.value, '/extensions'),
      activePaths: [resolveAppNavigationPath(isEmbedded.value, '/extensions')],
      icon: LibraryBig,
      activeIcon: LibraryBig
    })
  }

  if (userStore.hasPermission('dashboard:view')) {
    items.push({
      name: '数据总览',
      path: resolveAppNavigationPath(isEmbedded.value, '/dashboard'),
      icon: BarChart3,
      activeIcon: BarChart3
    })
  }

  return items
})

const primaryNavItem = computed(() => mainList.value[0] || null)
const secondaryNavItems = computed(() => mainList.value.slice(1))

const isNavItemActive = (item) => {
  const activePaths = item.activePaths || [item.path]
  if (item.exactActive) {
    return activePaths.some((path) => route.path === path)
  }
  return activePaths.some((path) => route.path === path || route.path.startsWith(`${path}/`))
}

const setSidebarCollapsed = (collapsed) => {
  if (isEmbedded.value) {
    embedSidebarCollapsed.value = collapsed
    return
  }
  sidebarCollapsed.value = collapsed
}

const toggleSidebar = () => {
  setSidebarCollapsed(!layoutSidebarCollapsed.value)
}

const openConversationSearch = () => {
  conversationSearchOpen.value = true
}

const initAgentNavigation = async () => {
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }
    await chatThreadsStore.loadThreads()
  } catch (error) {
    console.warn('加载对话导航失败:', error)
  }
}

/** 根据当前运行形态返回对话路由名。 */
const getAgentRouteName = (withThread = false) => {
  if (isEmbedded.value) return withThread ? 'EmbedAgentWithThreadId' : 'EmbedAgent'
  return withThread ? 'AgentCompWithThreadId' : 'AgentComp'
}

/** 在非对话页切回窄档前先恢复当前对话。 */
const requestEmbedMode = async (mode) => {
  if (mode !== 'fullscreen' && !isConversationRoute.value) {
    await router.push({
      name: getAgentRouteName(Boolean(currentThreadId.value)),
      params: currentThreadId.value ? { thread_id: currentThreadId.value } : undefined
    })
  }
  requestDisplayMode(mode, currentThreadId.value)
}

const requestEmbedClose = () => requestClose(currentThreadId.value)

const handleSelectChat = (threadId) => {
  if (!threadId) return
  chatThreadsStore.setCurrentThreadId(threadId)
  router.push({
    name: getAgentRouteName(true),
    params: { thread_id: threadId }
  })
}

const handleSearchThreadFound = (thread) => {
  chatThreadsStore.upsertThread(thread)
}

const handleSearchSelectThread = (thread) => {
  if (!thread?.id) return
  chatThreadsStore.upsertThread(thread)
  handleSelectChat(thread.id)
}

const handleCreateConversationFromSearch = () => {
  chatThreadsStore.setCurrentThreadId(null)
  router.push({ name: getAgentRouteName() })
}

const searchWorkspace = (query) => searchWorkspaceFiles(query)

// 侧边栏搜索到工作区文件后跳转到工作区并打开对应文件
const handleSearchSelectFile = (entry) => {
  if (!entry?.path) return
  router.push({
    path: resolveAppNavigationPath(isEmbedded.value, '/workspace'),
    query: { open: entry.path }
  })
}

const handleDeleteChat = async (threadId) => {
  if (!threadId) return
  try {
    await chatThreadsStore.deleteThread(threadId)
    if (route.params.thread_id === threadId) {
      await router.replace({ name: getAgentRouteName() })
    }
  } catch (error) {
    console.warn('删除对话失败:', error)
  }
}

const handleRenameChat = async ({ chatId, title }) => {
  try {
    await chatThreadsStore.updateThread(chatId, title)
  } catch (error) {
    console.warn('重命名对话失败:', error)
  }
}

const handleTogglePinChat = async (threadId) => {
  const thread = threads.value.find((item) => item.id === threadId)
  if (!thread) return
  try {
    await chatThreadsStore.updateThread(threadId, null, !thread.is_pinned)
    await chatThreadsStore.loadThreads()
    if (currentThreadId.value) {
      chatThreadsStore.setCurrentThreadId(currentThreadId.value)
    }
  } catch (error) {
    console.warn('更新置顶状态失败:', error)
  }
}

watch(
  () => [route.path, route.params.thread_id],
  () => {
    if (!isConversationRoute.value) return
    const threadId = typeof route.params.thread_id === 'string' ? route.params.thread_id : null
    chatThreadsStore.setCurrentThreadId(threadId)
  },
  { immediate: true }
)

// Provide settings modal methods to child components
provide('settingsModal', {
  openSettingsModal
})
</script>

<template>
  <div
    class="app-layout"
    :class="{
      'sidebar-collapsed': layoutSidebarCollapsed,
      'embed-layout': isEmbedded
    }"
  >
    <div v-if="showSidebar" class="header">
      <div class="sidebar-brand" @click.stop>
        <router-link
          v-if="!layoutSidebarCollapsed"
          :to="isEmbedded ? '/embed' : '/'"
          class="brand-link"
        >
          <img :src="infoStore.organization.avatar" class="brand-avatar" />
          <span class="brand-name">{{ organizationName }}</span>
        </router-link>
        <button
          v-else
          type="button"
          class="brand-link brand-expand-button"
          aria-label="展开侧边栏"
          @click="setSidebarCollapsed(false)"
        >
          <img :src="infoStore.organization.avatar" class="brand-avatar brand-avatar-image" />
          <PanelLeftOpen class="brand-expand-icon" size="20" />
        </button>
        <div
          v-if="!layoutSidebarCollapsed"
          class="sidebar-header-actions"
          aria-label="侧边栏操作"
        >
          <button
            type="button"
            class="sidebar-header-action"
            :class="{ active: conversationSearchOpen }"
            aria-label="搜索"
            @click="openConversationSearch"
          >
            <Search size="17" />
          </button>
          <button
            type="button"
            class="sidebar-header-action"
            aria-label="折叠侧边栏"
            @click="toggleSidebar"
          >
            <PanelLeft size="17" />
          </button>
        </div>
      </div>
      <div class="nav">
        <RouterLink
          v-if="primaryNavItem"
          :to="primaryNavItem.path"
          class="nav-item"
          :class="{ active: isNavItemActive(primaryNavItem) }"
          :active-class="primaryNavItem.action ? '' : 'active'"
          @click.stop
        >
          <a-tooltip placement="right" :open="layoutSidebarCollapsed ? undefined : false">
            <template #title>{{ primaryNavItem.name }}</template>
            <component
              class="icon"
              :is="
                isNavItemActive(primaryNavItem) ? primaryNavItem.activeIcon : primaryNavItem.icon
              "
              size="18"
            />
          </a-tooltip>
          <span class="nav-text">{{ primaryNavItem.name }}</span>
        </RouterLink>

        <button
          v-if="layoutSidebarCollapsed"
          type="button"
          class="nav-item"
          :class="{ active: conversationSearchOpen }"
          aria-label="搜索"
          @click.stop="openConversationSearch"
        >
          <a-tooltip placement="right" title="搜索">
            <Search class="icon" size="18" />
          </a-tooltip>
        </button>

        <RouterLink
          v-for="(item, index) in secondaryNavItems"
          :key="index"
          :to="item.path"
          v-show="!item.hidden"
          class="nav-item"
          :class="{ active: isNavItemActive(item) }"
          :active-class="item.action ? '' : 'active'"
          @click.stop
        >
          <a-tooltip placement="right" :open="layoutSidebarCollapsed ? undefined : false">
            <template #title>{{ item.name }}</template>
            <component
              class="icon"
              :is="isNavItemActive(item) ? item.activeIcon : item.icon"
              size="18"
            />
          </a-tooltip>
          <span class="nav-text">{{ item.name }}</span>
        </RouterLink>
      </div>
      <div class="fill">
        <ConversationNavSection
          v-if="canUseAgents && !layoutSidebarCollapsed"
          class="sidebar-conversations"
          :current-chat-id="activeConversationThreadId"
          :chats-list="threads"
          :has-more-chats="hasMoreThreads"
          :is-loading-more="isLoadingMoreThreads"
          @select-chat="handleSelectChat"
          @delete-chat="handleDeleteChat"
          @rename-chat="handleRenameChat"
          @toggle-pin="handleTogglePinChat"
          @load-more-chats="() => chatThreadsStore.loadMoreThreads()"
        />
      </div>
      <div class="foo">
        <!-- 用户信息组件 -->
        <div class="nav-item user-info" @click.stop>
          <UserInfoComponent :show-role="!layoutSidebarCollapsed" :allow-logout="!isEmbedded">
            <template v-if="userStore.hasPermission('system_task:manage')" #actions>
              <a-tooltip placement="top" title="任务中心">
                <button
                  class="user-task-center"
                  :class="{ active: isDrawerOpen }"
                  type="button"
                  aria-label="任务中心"
                  @click.stop="taskerStore.openDrawer()"
                >
                  <a-badge
                    :count="activeTaskCount"
                    :overflow-count="99"
                    class="task-center-badge"
                    size="small"
                  >
                    <ClipboardList class="icon" size="16" />
                  </a-badge>
                </button>
              </a-tooltip>
            </template>
          </UserInfoComponent>
        </div>
      </div>
    </div>
    <div
      v-if="isEmbedded && !embedAuthorized"
      id="app-router-view"
      class="embed-auth-waiting"
      role="status"
    >
      <span class="embed-auth-spinner" aria-hidden="true"></span>
      <span>{{ embedStatusMessage }}</span>
    </div>
    <router-view v-else v-slot="{ Component, route }" id="app-router-view">
      <keep-alive v-if="route.meta.keepAlive !== false">
        <component :is="Component" />
      </keep-alive>
      <component :is="Component" v-else />
    </router-view>
    <div
      v-if="isEmbedded && embedAuthorized && !isConversationRoute"
      class="embed-page-controls"
      role="group"
      aria-label="AI 助手显示模式"
      :aria-busy="!embedModeConfirmed"
    >
      <button
        v-for="option in embedModeOptions"
        :key="option.value"
        type="button"
        class="embed-page-control"
        :class="{ active: embedDisplayMode === option.value }"
        :title="option.label"
        :aria-label="`切换为${option.label}`"
        :aria-pressed="embedDisplayMode === option.value"
        @click="requestEmbedMode(option.value)"
      >
        <component :is="option.icon" :size="16" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="embed-page-control embed-page-close"
        title="关闭 AI 助手"
        aria-label="关闭 AI 助手"
        @click="requestEmbedClose"
      >
        <X :size="16" aria-hidden="true" />
      </button>
    </div>

    <GlobalSearchModal
      v-model:open="conversationSearchOpen"
      :modes="searchModes"
      :default-mode="canUseAgents ? 'conversation' : 'file'"
      :recent-threads="threads"
      :file-search="searchWorkspace"
      file-placeholder="搜索工作区文件..."
      @select-thread="handleSearchSelectThread"
      @create-thread="handleCreateConversationFromSearch"
      @thread-found="handleSearchThreadFound"
      @select-file="handleSearchSelectFile"
    />

    <!-- Debug Modal -->
    <a-modal
      v-model:open="showDebugModal"
      title="调试面板"
      width="90%"
      :footer="null"
      @cancel="handleDebugModalClose"
      :maskClosable="true"
      :destroyOnClose="true"
      class="debug-modal"
    >
      <DebugComponent />
    </a-modal>
    <TaskCenterDrawer v-if="userStore.hasPermission('system_task:manage')" />
  </div>
</template>

<style lang="less" scoped>
// Less 变量定义
@sidebar-width: 230px;
@sidebar-collapsed-width: 56px;
@sidebar-padding-y: 6px;
@sidebar-padding-x: 8px;
@sidebar-padding: @sidebar-padding-y @sidebar-padding-x;
@sidebar-border-width: 1px;
@sidebar-item-height: 32px;
@sidebar-item-padding-x: 10px;
@sidebar-icon-size: 16px;
@brand-avatar-size: 28px;
@sidebar-collapsed-content-width: @sidebar-collapsed-width - (2 * @sidebar-padding-x) -
  @sidebar-border-width;
@sidebar-collapsed-icon-padding-x: (
  (@sidebar-collapsed-content-width - @sidebar-icon-size - (2 * @sidebar-border-width)) / 2
);
@sidebar-collapsed-avatar-padding-x: (
  (@sidebar-collapsed-content-width - @sidebar-item-height - (2 * @sidebar-border-width)) / 2
);
@sidebar-collapsed-brand-padding-x: ((@sidebar-collapsed-content-width - @brand-avatar-size) / 2);
@sidebar-collapsed-brand-icon-padding-x: (
  (@sidebar-collapsed-content-width - @sidebar-icon-size) / 2
);

.app-layout {
  display: flex;
  flex-direction: row;
  width: 100%;
  height: 100vh;
  min-width: var(--min-width);
}

.app-layout.embed-layout {
  min-width: 0;
}

div.header,
#app-router-view {
  height: 100%;
  max-width: 100%;
}

#app-router-view {
  flex: 1 1 auto;
  overflow-y: auto;
}

.embed-auth-waiting {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 24px;
  color: var(--gray-600);
  background: var(--gray-25);
  font-size: 14px;
  text-align: center;
}

// OA 授权完成前使用轻量旋转指示，避免等待区域看起来像页面卡住。
.embed-auth-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--gray-200);
  border-top-color: var(--main-color);
  border-radius: 50%;
  animation: embed-auth-spin 0.8s linear infinite;
}

@keyframes embed-auth-spin {
  to {
    transform: rotate(360deg);
  }
}

.embed-page-controls {
  position: fixed;
  top: 10px;
  right: 14px;
  z-index: 20;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}

.embed-page-control {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--gray-600);
  background: transparent;
  cursor: pointer;
}

.embed-page-control:hover,
.embed-page-control:focus-visible,
.embed-page-control.active {
  color: var(--main-600);
  background: var(--main-50);
  outline: none;
}

.embed-page-close {
  margin-left: 2px;
}

.header {
  display: flex;
  flex-direction: column;
  flex: 0 0 @sidebar-width;
  justify-content: flex-start;
  align-items: stretch;
  gap: 16px;
  background-color: var(--main-5);
  height: 100%;
  width: @sidebar-width;
  border-right: 1px solid var(--gray-100);
  padding: @sidebar-padding;
  overflow: hidden;
  user-select: none;
  transition:
    width 0.18s ease,
    flex-basis 0.18s ease;

  .nav {
    display: flex;
    flex: 0 0 auto;
    flex-direction: column;
    justify-content: flex-start;
    align-items: stretch;
    position: relative;
    gap: 0;
  }

  .sidebar-conversations {
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }

  .sidebar-brand,
  :deep(.conversation-nav-section:not(.sidebar-conversations)),
  .user-info {
    flex-shrink: 0;
  }

  .fill {
    flex: 1 1 0;
    min-height: 0;
  }

  .sidebar-brand {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: @sidebar-item-height;
    gap: 8px;
  }

  .brand-link {
    display: flex;
    flex: 1 1 auto;
    align-items: center;
    min-width: 0;
    height: @sidebar-item-height;
    color: var(--gray-900);
    text-decoration: none;
    border: 0;
    background: transparent;
    padding: 0 4px;
    cursor: pointer;
  }

  .brand-avatar {
    flex: 0 0 @brand-avatar-size;
    width: @brand-avatar-size;
    height: @brand-avatar-size;
    border-radius: 6px;
    object-fit: cover;
  }

  .brand-name {
    min-width: 0;
    margin-left: 10px;
    overflow: hidden;
    color: var(--gray-1000);
    font-size: 15px;
    font-weight: 650;
    line-height: 20px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sidebar-header-actions {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 2px;
  }

  .sidebar-header-action {
    display: inline-flex;
    flex: 0 0 30px;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--gray-600);
    cursor: pointer;
    transition:
      background-color 0.2s ease,
      border-color 0.2s ease,
      color 0.2s ease;

    &:hover,
    &:focus-visible {
      background: var(--main-20);
      color: var(--main-color);
      outline: none;
    }
    &.active {
      background: var(--main-20);
      color: var(--main-color);
    }
  }

  .nav-item {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    width: 100%;
    height: @sidebar-item-height;
    padding: 0 @sidebar-item-padding-x;
    border: 1px solid transparent;
    border-radius: 8px;
    background-color: transparent;
    color: var(--gray-700);
    font-size: 14px;
    font-weight: 450;
    transition:
      background-color 0.2s ease-in-out,
      border-color 0.2s ease-in-out,
      color 0.2s ease-in-out;
    margin: 0;
    text-decoration: none;
    cursor: pointer;
    outline: none;

    .icon {
      flex: 0 0 @sidebar-icon-size;
      width: @sidebar-icon-size;
      height: @sidebar-icon-size;
    }

    .nav-text {
      min-width: 0;
      max-width: 140px;
      margin-left: 8px;
      overflow: hidden;
      line-height: 20px;
      font-weight: 450;
      text-overflow: ellipsis;
      white-space: nowrap;
      transition:
        opacity 0.12s ease,
        margin-left 0.18s ease,
        max-width 0.18s ease;
    }

    & > svg:focus {
      outline: none;
    }
    & > svg:focus-visible {
      outline: none;
    }

    &.active {
      border-color: transparent;
      background-color: color-mix(in srgb, var(--main-color) 6%, var(--gray-0));
      font-weight: 600;
      color: var(--main-color);
    }

    &.primary-action {
      margin-bottom: 8px;
      border-color: var(--gray-150);
      background-color: var(--gray-0);
      color: var(--main-color);
      box-shadow: 0 3px 4px rgba(0, 10, 20, 0.02);

      &:hover {
        border-color: var(--gray-200);
        background-color: var(--gray-0);
        color: var(--main-color);
        box-shadow: 0 3px 4px rgba(0, 10, 20, 0.07);
      }
    }

    &.warning {
      color: var(--color-error-500);
    }

    &:hover {
      border-color: transparent;
      background-color: var(--main-20);
      color: var(--main-color);
    }

    &.api-docs {
      padding: 10px 12px;
    }
    &.docs {
      display: none;
    }
    &.theme-toggle-nav {
      .theme-toggle-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        height: 100%;
        cursor: pointer;
        color: var(--gray-1000);
        transition: color 0.2s ease-in-out;

        &:hover {
          color: var(--main-color);
        }
      }
    }
    &.user-info {
      margin-bottom: 8px;
      padding: 0 3px;
      overflow: hidden;

      :deep(.user-info-component) {
        width: 100%;
      }

      :deep(.user-info-dropdown) {
        width: 100%;
        height: @sidebar-item-height;
        border-radius: 8px;
        transition:
          background-color 0.2s ease,
          color 0.2s ease;
      }

      :deep(.user-info-dropdown:hover) {
        background: var(--main-20);
        color: var(--main-color);
      }
      :deep(.user-name) {
        flex: 1 1 auto;
      }

      :deep(.user-task-center) {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        padding: 0;
        border: 1px solid transparent;
        border-radius: 6px;
        background: transparent;
        color: var(--gray-600);
        cursor: pointer;
        transition:
          background-color 0.2s ease,
          color 0.2s ease;

        &:hover,
        &.active {
          background: var(--main-30);
          color: var(--main-color);
        }

        .task-center-badge {
          display: flex;
          justify-content: center;
        }

        .icon {
          display: block;
          width: 16px;
          height: 16px;
        }
      }
    }
  }
}

.app-layout.sidebar-collapsed {
  .header {
    flex-basis: @sidebar-collapsed-width;
    width: @sidebar-collapsed-width;
    align-items: stretch;
    padding: @sidebar-padding;

    .sidebar-brand {
      justify-content: flex-start;
      width: 100%;
    }

    .brand-expand-button {
      flex: 0 0 100%;
      justify-content: flex-start;
      width: 100%;
      padding: 0;
      border-radius: 8px;

      .brand-avatar-image {
        margin-left: @sidebar-collapsed-brand-padding-x;
      }

      .brand-expand-icon {
        display: none;
        margin-left: @sidebar-collapsed-brand-icon-padding-x;
        width: @sidebar-icon-size;
        height: @sidebar-icon-size;
        color: var(--main-color);
      }

      &:hover,
      &:focus-visible {
        background: var(--main-20);
        outline: none;

        .brand-avatar-image {
          display: none;
        }

        .brand-expand-icon {
          display: block;
        }
      }
    }

    .nav {
      align-items: stretch;
      width: 100%;
    }

    .nav-item {
      justify-content: flex-start;
      width: 100%;
      padding: 0 @sidebar-collapsed-icon-padding-x;

      .nav-text {
        max-width: 0;
        margin-left: 0;
        opacity: 0;
        pointer-events: none;
      }

      &.user-info {
        padding: 0 @sidebar-collapsed-avatar-padding-x;

        :deep(.user-info-component),
        :deep(.user-info-dropdown) {
          justify-content: flex-start;
        }

        :deep(.user-info-actions) {
          display: none;
        }
      }
    }
  }
}
</style>
