<script setup>
import { ref, reactive, onMounted, computed, provide, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { GithubOutlined } from '@ant-design/icons-vue'
import {
  LibraryBig,
  BarChart3,
  ClipboardList,
  Blocks,
  Box,
  FolderKanban,
  PanelLeftClose,
  PanelLeftOpen,
  MessageCirclePlus
} from 'lucide-vue-next'

import { useConfigStore } from '@/stores/config'
import { useAgentStore } from '@/stores/agent'
import { useChatThreadsStore } from '@/stores/chatThreads'
import { useChatUIStore } from '@/stores/chatUI'
import { useDatabaseStore } from '@/stores/database'
import { useInfoStore } from '@/stores/info'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import { storeToRefs } from 'pinia'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import DebugComponent from '@/components/DebugComponent.vue'
import TaskCenterDrawer from '@/components/TaskCenterDrawer.vue'
import SettingsModal from '@/components/SettingsModal.vue'
import ConversationNavSection from '@/components/ConversationNavSection.vue'

const configStore = useConfigStore()
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const chatUIStore = useChatUIStore()
const databaseStore = useDatabaseStore()
const infoStore = useInfoStore()
const taskerStore = useTaskerStore()
const userStore = useUserStore()
const { activeCount: activeCountRef, isDrawerOpen } = storeToRefs(taskerStore)
const { threads, currentThreadId, hasMoreThreads, isLoadingMoreThreads } =
  storeToRefs(chatThreadsStore)

const layoutSettings = reactive({
  showDebug: false,
  useTopBar: false // 是否使用顶栏
})

// Add state for GitHub stars
const githubStars = ref(0)
const isLoadingStars = ref(false)

// Add state for debug modal
const showDebugModal = ref(false)

// Add state for settings modal
const showSettingsModal = ref(false)

const { sidebarCollapsed } = storeToRefs(chatUIStore)

// Provide settings modal methods to child components
const openSettingsModal = () => {
  showSettingsModal.value = true
}

// Handle debug modal close
const handleDebugModalClose = () => {
  showDebugModal.value = false
}

const getRemoteConfig = async () => {
  if (!userStore.isAdmin) return
  try {
    await configStore.refreshConfig()
  } catch (error) {
    console.warn('加载系统配置失败:', error)
  }
}

const getRemoteDatabase = async () => {
  try {
    await databaseStore.loadDatabases()
  } catch (error) {
    console.warn('加载知识库列表失败:', error)
  }
}

// Fetch GitHub stars count
const fetchGithubStars = async () => {
  try {
    isLoadingStars.value = true
    // 公共API，可以直接使用fetch
    const response = await fetch('https://api.github.com/repos/xerrors/Yuxi')
    const data = await response.json()
    githubStars.value = data.stargazers_count
  } catch (error) {
    console.error('获取GitHub stars失败:', error)
  } finally {
    isLoadingStars.value = false
  }
}

onMounted(async () => {
  // 加载信息配置与知识库数据无依赖，可并行
  await Promise.all([infoStore.loadInfoConfig(), getRemoteDatabase()])
  await initAgentNavigation()
  // 仅管理员加载系统配置和任务中心数据
  if (userStore.isAdmin) {
    await getRemoteConfig()
    taskerStore.loadTasks()
    fetchGithubStars() // Fetch GitHub stars on mount
  }
})

const route = useRoute()
const router = useRouter()

const activeTaskCount = computed(() => activeCountRef.value || 0)
const organizationName = computed(() => {
  return infoStore.organization.name || infoStore.branding.name || 'Yuxi'
})

// 下面是导航菜单部分，添加智能体项
const isLiteMode = import.meta.env.VITE_LITE_MODE === 'true'

const mainList = computed(() => {
  const items = [
    {
      name: '创建新对话',
      path: '/agent',
      icon: MessageCirclePlus,
      activeIcon: MessageCirclePlus,
      action: true
    }
  ]

  items.push({
    name: '工作区',
    path: '/workspace',
    icon: FolderKanban,
    activeIcon: FolderKanban
  })

  if (userStore.isAdmin) {
    if (!isLiteMode) {
      items.push({
        name: '知识库',
        path: '/database',
        activePaths: ['/database', '/graph'],
        icon: LibraryBig,
        activeIcon: LibraryBig
      })
    }

    items.push({
      name: '扩展管理',
      path: '/extensions',
      icon: Blocks,
      activeIcon: Blocks
    })

    items.push({
      name: '模型配置',
      path: '/model-config',
      icon: Box,
      activeIcon: Box
    })

    items.push({
      name: 'Dashboard',
      path: '/dashboard',
      icon: BarChart3,
      activeIcon: BarChart3
    })
  }

  return items
})

const isNavItemActive = (item) => {
  const activePaths = item.activePaths || [item.path]
  return activePaths.some((path) => route.path === path || route.path.startsWith(`${path}/`))
}

const setSidebarCollapsed = (collapsed) => {
  sidebarCollapsed.value = collapsed
}

const toggleSidebar = () => {
  setSidebarCollapsed(!sidebarCollapsed.value)
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

const handleSelectChat = (threadId) => {
  if (!threadId) return
  chatThreadsStore.setCurrentThreadId(threadId)
  router.push({ name: 'AgentCompWithThreadId', params: { thread_id: threadId } })
}

const handleDeleteChat = async (threadId) => {
  if (!threadId) return
  try {
    await chatThreadsStore.deleteThread(threadId)
    if (route.params.thread_id === threadId) {
      await router.replace({ name: 'AgentComp' })
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
    if (!route.path.startsWith('/agent')) return
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
    :class="{ 'use-top-bar': layoutSettings.useTopBar, 'sidebar-collapsed': sidebarCollapsed }"
  >
    <div class="header" :class="{ 'top-bar': layoutSettings.useTopBar }">
      <div class="sidebar-brand" @click.stop>
        <router-link v-if="!sidebarCollapsed" to="/" class="brand-link">
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
        <button
          v-if="!sidebarCollapsed"
          type="button"
          class="sidebar-toggle"
          aria-label="折叠侧边栏"
          @click="toggleSidebar"
        >
          <PanelLeftClose size="18" />
        </button>
      </div>
      <div class="nav">
        <!-- 使用mainList渲染导航项 -->
        <RouterLink
          v-for="(item, index) in mainList"
          :key="index"
          :to="item.path"
          v-show="!item.hidden"
          class="nav-item"
          :class="{ active: !item.action && isNavItemActive(item), 'primary-action': item.action }"
          :active-class="item.action ? '' : 'active'"
          @click.stop
        >
          <a-tooltip placement="right" :open="sidebarCollapsed ? undefined : false">
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
          v-if="!sidebarCollapsed"
          class="sidebar-conversations"
          :current-chat-id="currentThreadId"
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
        <div class="github nav-item" @click.stop>
          <a-tooltip placement="right" :open="sidebarCollapsed ? undefined : false">
            <template #title>欢迎 Star</template>
            <a href="https://github.com/xerrors/Yuxi" target="_blank" class="github-link">
              <GithubOutlined class="icon" />
              <span class="nav-text">GitHub</span>
              <span v-if="githubStars > 0" class="github-stars">
                <span class="star-count">{{ (githubStars / 1000).toFixed(1) }}k</span>
              </span>
            </a>
          </a-tooltip>
        </div>
        <!-- 用户信息组件 -->
        <div class="nav-item user-info" @click.stop>
          <UserInfoComponent :show-role="!sidebarCollapsed">
            <template v-if="userStore.isAdmin" #actions>
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
    <router-view v-slot="{ Component, route }" id="app-router-view">
      <keep-alive v-if="route.meta.keepAlive !== false">
        <component :is="Component" />
      </keep-alive>
      <component :is="Component" v-else />
    </router-view>

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
    <TaskCenterDrawer v-if="userStore.isAdmin" />
    <SettingsModal v-model:visible="showSettingsModal" @close="() => (showSettingsModal = false)" />
  </div>
</template>

<style lang="less" scoped>
// Less 变量定义
@sidebar-width: 252px;
@sidebar-collapsed-width: 56px;
@sidebar-padding: 6px 8px;
@sidebar-item-height: 36px;
@sidebar-item-padding-x: 10px;
@sidebar-icon-size: 18px;

.app-layout {
  display: flex;
  flex-direction: row;
  width: 100%;
  height: 100vh;
  min-width: var(--min-width);
}

div.header,
#app-router-view {
  height: 100%;
  max-width: 100%;
  user-select: none;
}

#app-router-view {
  flex: 1 1 auto;
  overflow-y: auto;
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
    gap: 4px;
  }

  .sidebar-conversations {
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }

  .sidebar-brand,
  :deep(.conversation-nav-section:not(.sidebar-conversations)),
  .github,
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
    padding: 0 6px;
    cursor: pointer;
  }

  .brand-avatar {
    flex: 0 0 28px;
    width: 28px;
    height: 28px;
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

  .sidebar-toggle {
    display: inline-flex;
    flex: 0 0 32px;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: var(--gray-600);
    cursor: pointer;
    transition:
      background-color 0.2s ease,
      border-color 0.2s ease,
      color 0.2s ease;

    &:hover,
    &:focus-visible {
      border-color: var(--main-50);
      background: var(--main-20);
      color: var(--main-color);
      outline: none;
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

    &.github {
      margin-bottom: 8px;
      &:hover {
        border-color: transparent;
      }

      .github-link {
        display: flex;
        align-items: center;
        width: 100%;
        min-width: 0;
        color: inherit;
        text-decoration: none;
      }

      .icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: @sidebar-icon-size;
        line-height: 1;
      }

      .github-stars {
        display: flex;
        align-items: center;
        max-width: 48px;
        margin-left: auto;
        overflow: hidden;
        font-size: 12px;
        color: var(--gray-500);
        white-space: nowrap;
        transition:
          opacity 0.12s ease,
          max-width 0.18s ease;

        .star-icon {
          color: var(--color-warning-500);
          font-size: 12px;
          margin-right: 2px;
        }

        .star-count {
          font-weight: 600;
        }
      }
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
      flex: 0 0 @sidebar-item-height;
      justify-content: center;
      width: @sidebar-item-height;
      padding: 0 6px;
      border-radius: 8px;

      .brand-expand-icon {
        display: none;
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
      width: @sidebar-item-height;
      padding: 0 10px;

      .nav-text,
      .github-stars {
        max-width: 0;
        margin-left: 0;
        opacity: 0;
        pointer-events: none;
      }

      &.github {
        .github-link {
          justify-content: flex-start;
        }
      }

      &.user-info {
        padding: 0;
        :deep(.user-info-actions) {
          display: none;
        }
      }
    }
  }
}

.app-layout.use-top-bar {
  flex-direction: column;
}

.header.top-bar {
  flex-direction: row;
  flex: 0 0 50px;
  width: 100%;
  height: 50px;
  border-right: none;
  border-bottom: 1px solid var(--main-40);
  background-color: var(--main-20);
  padding: 0 20px;
  gap: 24px;

  .logo {
    width: fit-content;
    height: 28px;
    margin-right: 16px;
    display: flex;
    align-items: center;

    a {
      display: flex;
      align-items: center;
      text-decoration: none;
      color: inherit;
    }

    img {
      width: 28px;
      height: 28px;
      margin-right: 8px;
    }
  }

  .nav {
    flex-direction: row;
    height: auto;
    gap: 20px;
  }

  .nav-item {
    flex-direction: row;
    width: auto;
    padding: 4px 16px;
    margin: 0;

    .icon {
      margin-right: 8px;
      font-size: 15px; // 减小图标大小
      border: none;
      outline: none;

      &:focus,
      &:active {
        border: none;
        outline: none;
      }
    }

    .text {
      margin-top: 0;
      font-size: 15px;
    }

    &.github {
      padding: 8px 12px;

      .icon {
        margin-right: 0;
        font-size: 18px;
      }

      &.active {
        color: var(--main-color);
      }

      a {
        display: flex;
        align-items: center;
      }

      .github-stars {
        display: flex;
        align-items: center;
        margin-left: 6px;

        .star-icon {
          color: var(--color-warning-500);
          font-size: 14px;
          margin-right: 2px;
        }
      }
    }

    &.theme-toggle-nav {
      padding: 8px 12px;

      .theme-toggle-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--gray-1000);
        transition: color 0.2s ease-in-out;
        cursor: pointer;

        &:hover {
          color: var(--main-color);
        }
      }

      &.active {
        .theme-toggle-icon {
          color: var(--main-color);
        }
      }
    }
  }
}
</style>
