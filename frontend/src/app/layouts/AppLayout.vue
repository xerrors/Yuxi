<script setup>
import { ref, onMounted, onUnmounted, computed, provide, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  BarChart3,
  ClipboardList,
  LibraryBig,
  Box,
  HardDrive,
  PanelLeft,
  PanelLeftOpen,
  MessageCircle,
  SquarePen,
  Search,
  Bot,
} from '@lucide/vue'

import { healthApi } from '@/apis/system_api'
import { useConfigStore } from '@/modules/settings/model/config'
import { useAgentStore } from '@/modules/agents/model/agent'
import { useChatThreadsStore } from '@/modules/session/model/chatThreads'
import { useChatUIStore } from '@/modules/session/model/chatUI'
import { useKnowledgeBaseStore } from '@/modules/knowledge/model/knowledgeBase'
import { useInfoStore } from '@/modules/settings/model/info'
import { useProjectsStore } from '@/modules/projects/model/projects'
import { useBackgroundJobsStore } from '@/modules/background-jobs/model/jobs'
import { useUserStore } from '@/modules/identity/model/user'
import { storeToRefs } from 'pinia'
import UserInfoComponent from '@/modules/settings/ui/UserInfoComponent.vue'
import BackgroundJobsDrawer from '@/modules/background-jobs/ui/BackgroundJobsDrawer.vue'
import SettingsModal from '@/modules/settings/ui/SettingsModal.vue'
import SessionNavigation from '@/modules/session/ui/SessionNavigation.vue'
import { createSessionRouteCache } from './sessionRouteCache'

const configStore = useConfigStore()
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const chatUIStore = useChatUIStore()
const knowledgeBaseStore = useKnowledgeBaseStore()
const infoStore = useInfoStore()
const projectsStore = useProjectsStore()
const jobsStore = useBackgroundJobsStore()
const userStore = useUserStore()
const { activeCount: activeCountRef, isDrawerOpen } = storeToRefs(jobsStore)
const { threadCreationInFlight } = storeToRefs(chatThreadsStore)

// Add state for settings modal
const personalAssistantEnabled = ref(false)
const showSettingsModal = ref(false)
const settingsInitialTab = ref('')

const route = useRoute()
const router = useRouter()
const isAgentView = computed(() => ['AgentComp', 'AgentCompWithThreadId'].includes(route.name))
const { sidebarCollapsed, lastThreadId, sessionsPinned, sessionPeekOpen } = storeToRefs(chatUIStore)
const normalNavigationRef = ref(null)
const sessionsVisible = computed(
  () => !sidebarCollapsed.value || (isAgentView.value && (sessionsPinned.value || sessionPeekOpen.value))
)
const compactViewport = useMediaQuery('(max-width: 767px)')
watch(
  compactViewport,
  (compact) => {
    if (compact) sidebarCollapsed.value = true
  },
  { immediate: true }
)

// Provide settings modal methods to child components
const openSettingsModal = (tab) => {
  settingsInitialTab.value = tab || (userStore.isSuperAdmin ? 'base' : 'account')
  showSettingsModal.value = true
}

const getRemoteConfig = async () => {
  try {
    await configStore.refreshConfig()
  } catch (error) {
    console.warn('加载系统配置失败:', error)
  }
}

const getRemoteKnowledgeBase = async () => {
  try {
    await knowledgeBaseStore.loadKnowledgeBases()
  } catch (error) {
    console.warn('加载知识库列表失败:', error)
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
  void healthApi.getDiscovery().then((data) => { personalAssistantEnabled.value = data?.capabilities?.features?.personal_assistant === true }).catch(() => {})
  // 各 Store 自行处理错误，导航不等待无依赖的品牌、知识库或配置请求。
  void infoStore.loadInfoConfig()
  void getRemoteKnowledgeBase()
  void initAgentNavigation()
  void getRemoteConfig()
  // 仅管理员加载后台作业数据
  if (userStore.isAdmin) {
    jobsStore.loadJobs()
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
      !sessionsVisible.value ||
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

const sessionRouteCache = createSessionRouteCache(router)
onUnmounted(sessionRouteCache.dispose)

const activeJobCount = computed(() => activeCountRef.value || 0)
const productName = computed(() => infoStore.branding.name || 'Yuxi')

// 下面是导航菜单部分，添加智能体项
const mainList = computed(() => {
  const items = [
    {
      name: '对话',
      path: lastThreadId.value
        ? router.resolve({ name: 'AgentCompWithThreadId', params: { thread_id: lastThreadId.value } }).path
        : '/agent',
      activePaths: ['/agent'],
      icon: MessageCircle,
      activeIcon: MessageCircle
    }
  ]

  if (personalAssistantEnabled.value) {
    items.push({
      name: '个人助理',
      path: '/assistant',
      icon: Bot,
      activeIcon: Bot
    })
  }

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

  items.push({
    name: userStore.isAdmin ? '知识库 · 技能' : '技能',
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
  sessionsPinned.value = false
  sessionPeekOpen.value = false
}

const toggleSidebar = () => {
  setSidebarCollapsed(!sidebarCollapsed.value)
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


watch(
  () => [route.path, route.params.thread_id],
  () => {
    if (!isAgentView.value) return
    if (threadCreationInFlight.value) return
    const threadId = typeof route.params.thread_id === 'string' ? route.params.thread_id : null
    chatThreadsStore.setCurrentThreadId(threadId)
  },
  { immediate: true }
)

// Provide settings modal methods to child components
provide('settingsModal', {
  openSettingsModal,
  closeSettingsModal: () => { showSettingsModal.value = false }
})
</script>

<template>
  <div
    class="app-layout"
    :class="{ 'sidebar-collapsed': sidebarCollapsed }"
  >
    <div class="header">
      <div class="sidebar-brand" @click.stop>
        <router-link v-if="!sidebarCollapsed" to="/" class="brand-link">
          <img :src="infoStore.branding.logo" alt="" class="brand-logo" />
          <span class="brand-name">{{ productName }}</span>
        </router-link>
        <button
          v-else
          type="button"
          class="brand-link brand-expand-button"
          aria-label="展开侧边栏"
          @click="setSidebarCollapsed(false)"
        >
          <img :src="infoStore.branding.logo" alt="" class="brand-logo brand-logo-image" />
          <PanelLeftOpen class="brand-expand-icon" size="20" />
        </button>
        <div v-if="!sidebarCollapsed" class="sidebar-header-actions" aria-label="侧边栏操作">
          <button
            type="button"
            class="sidebar-header-action"
            :class="{ active: normalNavigationRef?.sessionSearchOpen }"
            aria-label="搜索"
            @click="normalNavigationRef?.openSessionSearch()"
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
        <div v-if="primaryNavItem" class="primary-nav-row">
          <RouterLink
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
          <RouterLink
            v-if="!sidebarCollapsed"
            :to="{ name: 'AgentComp' }"
            class="primary-new-chat"
            active-class=""
            aria-label="新建对话"
            title="新建对话"
          >
            <SquarePen :size="16" aria-hidden="true" />
          </RouterLink>
        </div>

        <RouterLink
          v-for="(item, index) in secondaryNavItems"
          :key="index"
          :to="item.path"
          v-show="!item.hidden"
          class="nav-item"
          :class="{ active: isNavItemActive(item) }"
          :aria-label="item.name"
          :active-class="item.action ? '' : 'active'"
          @click.stop
        >
          <a-tooltip placement="right" :open="sidebarCollapsed ? undefined : false">
            <template #title>{{ item.name }}</template>
            <component
              class="icon"
              :is="isNavItemActive(item) ? item.activeIcon : item.icon"
              size="18"
              aria-hidden="true"
            />
          </a-tooltip>
          <span class="nav-text">{{ item.name }}</span>
        </RouterLink>
      </div>
      <div class="fill">
        <SessionNavigation
          ref="normalNavigationRef"
          v-show="!sidebarCollapsed"
          :visible="!sidebarCollapsed"
        />
      </div>
      <div class="foo">
        <!-- 用户信息组件 -->
        <div class="nav-item user-info" @click.stop>
          <UserInfoComponent :show-role="!sidebarCollapsed">
            <template v-if="userStore.isAdmin" #actions>
              <a-tooltip placement="top" title="后台作业">
                <button
                  class="user-job-center"
                  :class="{ active: isDrawerOpen }"
                  type="button"
                  aria-label="后台作业"
                  @click.stop="jobsStore.openDrawer()"
                >
                  <a-badge
                    :count="activeJobCount"
                    :overflow-count="99"
                    class="job-center-badge"
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
    <main id="app-router-view">
      <router-view v-slot="{ Component, route }">
        <keep-alive>
          <component
            :is="Component"
            v-if="route.meta.keepAlive !== false"
            :key="sessionRouteCache.key(route)"
            @session-created="sessionRouteCache.adopt"
          />
        </keep-alive>
        <component :is="Component" v-if="route.meta.keepAlive === false" />
      </router-view>
    </main>

    <BackgroundJobsDrawer v-if="userStore.isAdmin" />
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
@sidebar-collapsed-width: 52px;
@sidebar-padding-y: 6px;
@sidebar-padding-x: 8px;
@sidebar-border-width: 1px;
@sidebar-item-height: 32px;
@sidebar-item-padding-x: 6px;
@sidebar-icon-size: 16px;
@brand-logo-size: 24px;
@sidebar-collapsed-content-width: @sidebar-collapsed-width - @sidebar-border-width;
@sidebar-collapsed-icon-padding-x: (
  (@sidebar-collapsed-content-width - @sidebar-icon-size - (2 * @sidebar-border-width)) / 2
);
@sidebar-collapsed-brand-padding-x: ((@sidebar-collapsed-content-width - @brand-logo-size) / 2);
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
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}

.header {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 0 0 @sidebar-width;
  justify-content: flex-start;
  align-items: stretch;
  gap: 0;
  background-color: var(--bg-sider);
  height: 100%;
  width: @sidebar-width;
  border-right: 1px solid var(--gray-100);
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
    padding: 0 @sidebar-padding-x;
  }

  .sidebar-brand,
  :deep(.session-nav-section:not(.sidebar-sessions)),
  .github,
  .user-info {
    flex-shrink: 0;
  }

  .fill {
    display: flex;
    flex-direction: column;
    flex: 1 1 0;
    min-height: 0;
  }

  .foo {
    position: relative;
    z-index: 1;
    flex: 0 0 auto;
    margin-top: auto;
    background: var(--bg-sider);
    padding: 0 @sidebar-padding-x @sidebar-padding-y;
  }

  .sidebar-brand {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: @sidebar-item-height;
    margin-top: @sidebar-padding-y;
    padding: 0 @sidebar-padding-x;
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

  .brand-logo {
    flex: 0 0 @brand-logo-size;
    width: @brand-logo-size;
    height: @brand-logo-size;
    border-radius: 6px;
    object-fit: contain;
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
      background: var(--bg-sider-hover);
      color: var(--main-color);
      outline: none;
    }
    &.active {
      background: var(--bg-sider-hover);
      color: var(--main-color);
    }
  }

  .primary-nav-row {
    position: relative;

    .nav-item {
      padding-right: 38px;
    }

    &:hover .primary-new-chat,
    &:has(:focus-visible) .primary-new-chat {
      opacity: 1;
      pointer-events: auto;
    }
  }

  .primary-new-chat {
    position: absolute;
    top: 2px;
    right: 2px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: var(--gray-600);
    opacity: 0;
    pointer-events: none;
    transition: color 0.15s ease;

    &:hover,
    &:focus-visible {
      color: var(--gray-1000);
    }

    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: -2px;
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
      box-shadow: 0 3px 4px var(--shadow-0);

      &:hover {
        border-color: var(--gray-200);
        background-color: var(--gray-0);
        color: var(--gray-900);
        box-shadow: 0 3px 4px var(--shadow-2);
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
      height: 40px;
      padding: 0;
      overflow: hidden;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;

      &:hover,
      &:focus-within,
      &:has(.is-open) {
        border-radius: 8px;
        background: var(--gray-50);
        box-shadow: inset 0 0 0 1px var(--gray-200);
      }

      :deep(.user-info-component) {
        width: 100%;
      }

      :deep(.user-info-dropdown) {
        width: 100%;
        height: 40px;
        padding: 0 4px;
        box-sizing: border-box;
        border-radius: 8px;
        color: var(--gray-800);
        transition:
          background-color 0.2s ease,
          color 0.2s ease;
      }

      :deep(.user-info-dropdown:hover) {
        background: transparent;
        color: var(--gray-900);
      }
      :deep(.user-name) {
        flex: 1 1 auto;
        color: var(--gray-800);
        font-size: 13px;
        font-weight: 500;
      }

      :deep(.user-avatar) {
        box-shadow: none;
      }

      :deep(.avatar-image) {
        border-width: 1px;
      }

      :deep(.user-job-center) {
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
          border-color: var(--gray-200);
          background: var(--gray-100);
          color: var(--gray-900);
        }

        &:focus-visible {
          outline: 2px solid var(--gray-500);
          outline-offset: 1px;
        }

        .job-center-badge {
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
    overflow: visible;
    flex-basis: @sidebar-collapsed-width;
    width: @sidebar-collapsed-width;
    align-items: stretch;

    .sidebar-brand {
      justify-content: flex-start;
      width: 100%;
      margin-top: @sidebar-padding-y;
      padding: 0;
    }

    .brand-expand-button {
      flex: 0 0 100%;
      justify-content: flex-start;
      width: 100%;
      padding: 0;
      border-radius: 8px;

      .brand-logo-image {
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
        background: var(--bg-sider-hover);
        outline: none;

        .brand-logo-image {
          display: none;
        }

        .brand-expand-icon {
          display: block;
        }
      }
    }

    .primary-nav-row {
      width: @sidebar-item-height;
    }

    .nav {
      align-items: center;
      width: 100%;
      padding: 0;

      .nav-item {
        width: @sidebar-item-height;
        padding: 0;
        justify-content: center;
      }
    }

    .foo {
      padding: 0 0 @sidebar-padding-y;
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
        // 收起时用户卡片收窄居中，与图标导航项一致，避免悬停卡片贴满侧栏两边
        width: 40px;
        margin: 0 auto;
        padding: 0;

        :deep(.user-info-dropdown) {
          padding: 0;
        }

        :deep(.user-info-actions) {
          display: none;
        }
      }
    }
  }
}
@media (prefers-reduced-motion: reduce) {
  .header {
    transition: none;
  }
}
@media (max-width: 767px) {
  .app-layout {
    min-width: 0;
  }
}
</style>
