<script setup>
import { ref, reactive, onMounted, computed, provide } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { GithubOutlined } from '@ant-design/icons-vue'
import { Bot, Waypoints, LibraryBig, BarChart3, CircleCheck, Blocks } from 'lucide-vue-next'

import { useConfigStore } from '@/stores/config'
import { useDatabaseStore } from '@/stores/database'
import { useInfoStore } from '@/stores/info'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import { storeToRefs } from 'pinia'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import DebugComponent from '@/components/DebugComponent.vue'
import TaskCenterDrawer from '@/components/TaskCenterDrawer.vue'
import SettingsModal from '@/components/SettingsModal.vue'

const configStore = useConfigStore()
const databaseStore = useDatabaseStore()
const infoStore = useInfoStore()
const taskerStore = useTaskerStore()
const userStore = useUserStore()
const { activeCount: activeCountRef, isDrawerOpen } = storeToRefs(taskerStore)

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
    const response = await fetch('https://api.github.com/repos/xerrors/Yuxi-Know')
    const data = await response.json()
    githubStars.value = data.stargazers_count
  } catch (error) {
    console.error('获取GitHub stars失败:', error)
  } finally {
    isLoadingStars.value = false
  }
}

onMounted(async () => {
  // 加载信息配置
  await infoStore.loadInfoConfig()
  // 加载知识库数据（普通用户加载可访问知识库）
  await getRemoteDatabase()
  // 仅管理员加载系统配置和任务中心数据
  if (userStore.isAdmin) {
    await getRemoteConfig()
    taskerStore.loadTasks()
    fetchGithubStars() // Fetch GitHub stars on mount
  }
})

// 打印当前页面的路由信息，使用 vue3 的 setup composition API
const route = useRoute()
console.log(route)

const activeTaskCount = computed(() => activeCountRef.value || 0)

// 下面是导航菜单部分，添加智能体项
const isLiteMode = import.meta.env.VITE_LITE_MODE === 'true'

const mainList = computed(() => {
  const items = [
    {
      name: '智能体',
      path: '/agent',
      icon: Bot,
      activeIcon: Bot
    }
  ]

  if (userStore.isAdmin) {
    if (!isLiteMode) {
      items.push(
        {
          name: '图谱',
          path: '/graph',
          icon: Waypoints,
          activeIcon: Waypoints
        },
        {
          name: '知识库',
          path: '/database',
          icon: LibraryBig,
          activeIcon: LibraryBig
        }
      )
    }

    if (userStore.isSuperAdmin) {
      items.push({
        name: '扩展管理',
        path: '/extensions',
        icon: Blocks,
        activeIcon: Blocks
      })
    }

    items.push({
      name: 'Dashboard',
      path: '/dashboard',
      icon: BarChart3,
      activeIcon: BarChart3
    })
  }

  return items
})

// Provide settings modal methods to child components
provide('settingsModal', {
  openSettingsModal
})
</script>

<template>
  <div class="app-layout" :class="{ 'use-top-bar': layoutSettings.useTopBar }">
    <div v-if="userStore.isAdmin" class="header" :class="{ 'top-bar': layoutSettings.useTopBar }">
      <div class="logo circle">
        <router-link to="/">
          <img :src="infoStore.organization.avatar" />
        </router-link>
      </div>
      <div class="nav">
        <!-- 使用mainList渲染导航项 -->
        <RouterLink
          v-for="(item, index) in mainList"
          :key="index"
          :to="item.path"
          v-show="!item.hidden"
          class="nav-item"
          active-class="active"
        >
          <a-tooltip placement="right">
            <template #title>{{ item.name }}</template>
            <component
              class="icon"
              :is="route.path.startsWith(item.path) ? item.activeIcon : item.icon"
              size="22"
            />
          </a-tooltip>
        </RouterLink>
        <div
          v-if="userStore.isAdmin"
          class="nav-item task-center"
          :class="{ active: isDrawerOpen }"
          @click="taskerStore.openDrawer()"
        >
          <a-tooltip placement="right">
            <template #title>任务中心</template>
            <a-badge
              :count="activeTaskCount"
              :overflow-count="99"
              class="task-center-badge"
              size="small"
            >
              <CircleCheck class="icon" size="22" />
            </a-badge>
          </a-tooltip>
        </div>
      </div>
      <div class="fill"></div>
      <div class="github nav-item">
        <a-tooltip placement="right">
          <template #title>欢迎 Star</template>
          <a href="https://github.com/xerrors/Yuxi-Know" target="_blank" class="github-link">
            <GithubOutlined class="icon" />
            <span v-if="githubStars > 0" class="github-stars">
              <span class="star-count">{{ (githubStars / 1000).toFixed(1) }}k</span>
            </span>
          </a>
        </a-tooltip>
      </div>
      <!-- 用户信息组件 -->
      <div class="nav-item user-info">
        <UserInfoComponent />
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
@header-width: 50px;

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
  flex: 0 0 @header-width;
  justify-content: flex-start;
  align-items: center;
  background-color: var(--main-0);
  height: 100%;
  width: @header-width;
  border-right: 1px solid var(--gray-100);

  .nav {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    position: relative;
    // height: 45px;
    gap: 16px;
  }

  .fill {
    flex-grow: 1;
  }

  .logo {
    width: 34px;
    height: 34px;
    margin: 6px 0 20px 0;

    img {
      width: 100%;
      height: 100%;
      border-radius: 4px; // 50% for circle
    }

    & > a {
      text-decoration: none;
      font-size: 24px;
      font-weight: bold;
      color: var(--gray-900);
    }
  }

  .nav-item {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    padding: 4px;
    border: 1px solid transparent;
    border-radius: 12px;
    background-color: transparent;
    color: var(--gray-1000);
    font-size: 20px;
    transition:
      background-color 0.2s ease-in-out,
      color 0.2s ease-in-out;
    margin: 0;
    text-decoration: none;
    cursor: pointer;
    outline: none;

    & > svg:focus {
      outline: none;
    }
    & > svg:focus-visible {
      outline: none;
    }

    &.active {
      background-color: var(--gray-100);
      font-weight: bold;
      color: var(--main-color);
    }

    &.warning {
      color: var(--color-error-500);
    }

    &:hover {
      color: var(--main-color);
    }

    &.github {
      padding: 10px 12px;
      margin-bottom: 16px;
      &:hover {
        background-color: transparent;
        border: 1px solid transparent;
      }

      .github-link {
        display: flex;
        flex-direction: column;
        align-items: center;
        color: inherit;
      }

      .github-stars {
        display: flex;
        align-items: center;
        font-size: 12px;
        margin-top: 4px;

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
    &.task-center {
      .task-center-badge {
        width: 100%;
        display: flex;
        justify-content: center;
      }
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
