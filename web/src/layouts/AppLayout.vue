<script setup>
import { ref, onMounted, onUnmounted, computed, provide, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { GithubOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import {
  BarChart3,
  ClipboardList,
  LibraryBig,
  Box,
  HardDrive,
  PanelLeft,
  PanelLeftOpen,
  MessageCirclePlus,
  Search,
  GraduationCap
} from '@lucide/vue'

import { useConfigStore } from '@/stores/config'
import { useAgentStore } from '@/stores/agent'
import { useChatThreadsStore } from '@/stores/chatThreads'
import { useChatUIStore } from '@/stores/chatUI'
import { useDatabaseStore } from '@/stores/database'
import { useInfoStore } from '@/stores/info'
import { useProjectsStore } from '@/stores/projects'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import { storeToRefs } from 'pinia'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import TaskCenterDrawer from '@/components/TaskCenterDrawer.vue'
import SettingsModal from '@/components/SettingsModal.vue'
import ConversationNavSection from '@/components/ConversationNavSection.vue'
import GlobalSearchModal from '@/components/GlobalSearchModal.vue'
import { searchWorkspaceFiles } from '@/apis/workspace_api'
import { projectApi } from '@/apis/project_api'

const configStore = useConfigStore()
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const chatUIStore = useChatUIStore()
const databaseStore = useDatabaseStore()
const infoStore = useInfoStore()
const projectsStore = useProjectsStore()
const taskerStore = useTaskerStore()
const userStore = useUserStore()
const { activeCount: activeCountRef, isDrawerOpen } = storeToRefs(taskerStore)
const { projects, isLoading: projectsLoading, error: projectsError } = storeToRefs(projectsStore)
const { threads, currentThreadId, hasMoreThreads, isLoadingMoreThreads, threadCreationInFlight } =
  storeToRefs(chatThreadsStore)

// Add state for GitHub stars
const githubStars = ref(0)
const isLoadingStars = ref(false)

// Add state for settings modal
const showSettingsModal = ref(false)
const settingsInitialTab = ref('')

const { sidebarCollapsed } = storeToRefs(chatUIStore)
const conversationSearchOpen = ref(false)
const projectPendingId = ref(null)

// Provide settings modal methods to child components
const openSettingsModal = (tab) => {
  settingsInitialTab.value = tab || (userStore.isAdmin ? 'base' : 'account')
  showSettingsModal.value = true
}

const getRemoteConfig = async () => {
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

const handleGlobalKeydown = (e) => {
  // Ctrl+Shift+D or Cmd+Shift+D: Toggle Debug Modal for SuperAdmin
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'D' || e.key === 'd')) {
    if (userStore.isSuperAdmin) {
      e.preventDefault()
      infoStore.showDebugModal = !infoStore.showDebugModal
    }
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleGlobalKeydown)
  // 各 Store 自行处理错误，导航不等待无依赖的品牌、知识库或配置请求。
  void infoStore.loadInfoConfig()
  void getRemoteDatabase()
  void initAgentNavigation()
  void getRemoteConfig()
  // 仅管理员加载任务中心数据
  if (userStore.isAdmin) {
    taskerStore.loadTasks()
    fetchGithubStars() // Fetch GitHub stars on mount
  }
  startThreadStatusSync()
})

// 低频刷新侧边栏线程状态，让后台线程完成时也能从 loading 转为 ready/done。
const THREAD_STATUS_SYNC_INTERVAL_MS = 12 * 1000
let threadStatusSyncTimer = null

const startThreadStatusSync = () => {
  if (threadStatusSyncTimer) return
  threadStatusSyncTimer = setInterval(() => {
    if (
      sidebarCollapsed.value ||
      (typeof document !== 'undefined' && document.visibilityState !== 'visible')
    ) {
      return
    }
    void chatThreadsStore.syncThreadStatuses()
  }, THREAD_STATUS_SYNC_INTERVAL_MS)
}

onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
  if (threadStatusSyncTimer) {
    clearInterval(threadStatusSyncTimer)
    threadStatusSyncTimer = null
  }
})

const route = useRoute()
const router = useRouter()

const activeTaskCount = computed(() => activeCountRef.value || 0)
const activeConversationThreadId = computed(() => {
  return route.path.startsWith('/agent') ? currentThreadId.value : null
})
const organizationName = computed(() => {
  return infoStore.organization.name || infoStore.branding.name || '知伴'
})

// 下面是导航菜单部分，添加智能体项
const mainList = computed(() => {
  const items = [
    {
      name: '新建对话',
      path: '/agent',
      icon: MessageCirclePlus,
      activeIcon: MessageCirclePlus,
      action: true,
      exactActive: true
    }
  ]

  items.push({
    name: '智能体',
    path: '/agent-manage',
    icon: Box,
    activeIcon: Box
  })

  items.push({
    name: '个人空间',
    path: '/workspace',
    icon: HardDrive,
    activeIcon: HardDrive
  })

  if (
    userStore.businessRoles.includes('counselor') ||
    userStore.businessRoles.includes('business_admin')
  ) {
    items.push({
      name: '学生档案',
      path: '/students',
      icon: GraduationCap,
      activeIcon: GraduationCap
    })
  }

  items.push({
    name: '知识库 · 技能',
    path: '/extensions',
    activePaths: ['/extensions'],
    icon: LibraryBig,
    activeIcon: LibraryBig
  })

  if (userStore.isSuperAdmin) {
    items.push({
      name: '数据总览',
      path: '/dashboard',
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
  sidebarCollapsed.value = collapsed
}

const toggleSidebar = () => {
  setSidebarCollapsed(!sidebarCollapsed.value)
}

const openConversationSearch = () => {
  conversationSearchOpen.value = true
}

const initAgentNavigation = async () => {
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }
    await Promise.all([chatThreadsStore.loadThreads(), loadProjects()])
  } catch (error) {
    console.warn('加载对话导航失败:', error)
  }
}

const loadProjects = async () => {
  try {
    await projectsStore.loadProjects()
  } catch (error) {
    console.warn('加载项目导航失败:', error)
  }
}

const handleSelectChat = (threadId) => {
  if (!threadId) return
  if (!chatThreadsStore.setCurrentThreadId(threadId)) return
  router.push({ name: 'AgentCompWithThreadId', params: { thread_id: threadId } })
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
  if (!chatThreadsStore.setCurrentThreadId(null)) return
  router.push({ name: 'AgentComp' })
}

const handleCreateProjectChat = async (projectId) => {
  if (!projectId || projectPendingId.value || threadCreationInFlight.value) return
  await router.push({ name: 'AgentComp', query: { project_id: projectId } })
  chatThreadsStore.setCurrentThreadId(null)
}

const searchWorkspace = (query) => searchWorkspaceFiles(query)

// 侧边栏搜索到工作区文件后跳转到工作区并打开对应文件
const handleSearchSelectFile = (entry) => {
  if (!entry?.path) return
  router.push({ name: 'WorkspaceComp', query: { open: entry.path } })
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

const handleRenameProject = async ({ projectId, name }) => {
  if (!projectId || projectPendingId.value) return
  projectPendingId.value = projectId
  try {
    const updatedProject = await projectApi.renameProject(projectId, name)
    projectsStore.replaceProject(updatedProject)
    message.success('项目已重命名')
  } catch (error) {
    message.error(error?.message || '重命名项目失败')
  } finally {
    projectPendingId.value = null
  }
}

const handleDeleteProject = async (projectId) => {
  if (!projectId || projectPendingId.value) return
  projectPendingId.value = projectId
  try {
    await projectApi.deleteProject(projectId)
    const removedThreadIds = chatThreadsStore.removeThreadsByProject(projectId)
    projectsStore.removeProject(projectId)
    if (removedThreadIds.includes(route.params.thread_id)) {
      await router.replace({ name: 'AgentComp' })
    }
    message.success('项目及其中对话已删除，项目文件夹已保留')
  } catch (error) {
    message.error(error?.message || '删除项目失败')
  } finally {
    projectPendingId.value = null
  }
}

watch(
  () => [route.path, route.params.thread_id],
  () => {
    if (!route.path.startsWith('/agent')) return
    if (threadCreationInFlight.value) return
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
  <div class="app-layout" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <div class="header">
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
        <div v-if="!sidebarCollapsed" class="sidebar-header-actions" aria-label="侧边栏操作">
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
          <a-tooltip placement="right" :open="sidebarCollapsed ? undefined : false">
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
          v-if="sidebarCollapsed"
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
          :current-chat-id="activeConversationThreadId"
          :chats-list="threads"
          :projects="projects"
          :projects-loading="projectsLoading"
          :projects-error="projectsError"
          :project-pending-id="projectPendingId"
          :has-more-chats="hasMoreThreads"
          :is-loading-more="isLoadingMoreThreads"
          @select-chat="handleSelectChat"
          @delete-chat="handleDeleteChat"
          @rename-chat="handleRenameChat"
          @toggle-pin="handleTogglePinChat"
          @rename-project="handleRenameProject"
          @delete-project="handleDeleteProject"
          @create-project-chat="handleCreateProjectChat"
          @retry-projects="loadProjects"
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

    <GlobalSearchModal
      v-model:open="conversationSearchOpen"
      :modes="['conversation', 'file']"
      default-mode="conversation"
      :recent-threads="threads"
      :file-search="searchWorkspace"
      file-placeholder="搜索个人空间文件..."
      @select-thread="handleSearchSelectThread"
      @create-thread="handleCreateConversationFromSearch"
      @thread-found="handleSearchThreadFound"
      @select-file="handleSearchSelectFile"
    />

    <TaskCenterDrawer v-if="userStore.isAdmin" />
    <SettingsModal
      v-model:visible="showSettingsModal"
      :initial-tab="settingsInitialTab"
      @close="() => (showSettingsModal = false)"
    />
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

div.header,
#app-router-view {
  height: 100%;
  max-width: 100%;
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
  gap: 0;
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
    gap: 2px;
    margin-top: 12px;
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

  .foo {
    position: relative;
    z-index: 1;
    flex: 0 0 auto;
    background: var(--main-5);
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

    &.primary-action {
      margin-bottom: 8px;
      border-color: var(--gray-150);
      background-color: var(--gray-0);
      color: var(--main-color);
      box-shadow: 0 3px 4px rgba(0, 10, 20, 0.02);

      &:hover {
        border-color: var(--gray-200);
        background-color: var(--gray-0);
        color: var(--gray-900);
        box-shadow: 0 3px 4px rgba(0, 10, 20, 0.07);
      }
    }

    &.warning {
      color: var(--color-error-500);
    }

    &:hover {
      border-color: transparent;
      background-color: var(--gray-50);
      color: var(--gray-900);
    }

    &.active {
      border-color: transparent;
      background-color: color-mix(in srgb, var(--gray-100) 6%, var(--gray-100));
      font-weight: 600;
      color: var(--gray-1000);
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
        color: var(--gray-600);
        background-color: var(--gray-100);
        padding: 2px 8px;
        border-radius: 6px;
        white-space: nowrap;
        transition:
          opacity 0.12s ease,
          max-width 0.18s ease;

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
