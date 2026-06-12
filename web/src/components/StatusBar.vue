<template>
  <div class="status-bar">
    <div class="status-bar-content">
      <!-- 左侧：系统信息 -->
      <div class="status-left">
        <div class="system-info">
          <div class="system-details">
            <div class="system-name">{{ branding.name }}</div>
            <div class="system-subtitle">{{ branding.subtitle }}</div>
          </div>
        </div>
      </div>

      <!-- 右侧：时间和用户信息 -->
      <div class="status-right">
        <div class="time-info">
          <Clock class="icon" />
          <span class="current-time">{{ currentTime }}</span>
        </div>
        <div class="user-info">
          <User class="icon" />
          <span class="user-greeting">{{ greeting }}</span>
        </div>
        <div class="header-actions">
          <a-tooltip title="系统设置">
            <button
              type="button"
              class="header-action-button"
              aria-label="系统设置"
              @click="openSettings"
            >
              <Settings class="icon" />
            </button>
          </a-tooltip>
          <a-tooltip :title="themeStore.isDark ? '切换到浅色模式' : '切换到深色模式'">
            <button
              type="button"
              class="header-action-button"
              aria-label="切换主题"
              @click="toggleTheme"
            >
              <Sun v-if="themeStore.isDark" class="icon" />
              <Moon v-else class="icon" />
            </button>
          </a-tooltip>
          <a-tooltip title="任务中心">
            <button
              type="button"
              class="header-action-button task-center-button"
              :class="{ active: taskerStore.isDrawerOpen }"
              aria-label="任务中心"
              @click="openTaskCenter"
            >
              <ClipboardList class="icon" />
              <span class="task-center-label">任务中心</span>
              <a-badge
                :count="activeTaskCount"
                :overflow-count="99"
                class="task-center-badge"
                size="small"
              />
            </button>
          </a-tooltip>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, inject, onMounted, onUnmounted } from 'vue'
import { useInfoStore } from '@/stores/info'
import { useUserStore } from '@/stores/user'
import { Clock, User, ClipboardList, Settings, Sun, Moon } from 'lucide-vue-next'
import { useTaskerStore } from '@/stores/tasker'
import { useThemeStore } from '@/stores/theme'
import { storeToRefs } from 'pinia'
import dayjs from '@/utils/time'

// 使用 stores
const infoStore = useInfoStore()
const userStore = useUserStore()
const taskerStore = useTaskerStore()
const themeStore = useThemeStore()
const { activeCount: activeCountRef } = storeToRefs(taskerStore)
const { openSettingsModal } = inject('settingsModal', {})

// 响应式数据
const currentTime = ref('')

// 计算属性
const branding = computed(() => infoStore.branding)

// 用户名计算属性
const currentUser = computed(() => {
  return userStore.username || '游客'
})

// 问候语计算属性
const greeting = computed(() => {
  const hour = dayjs().tz('Asia/Shanghai').hour()
  let greetingText

  if (hour >= 5 && hour < 12) {
    greetingText = '早上好'
  } else if (hour >= 12 && hour < 14) {
    greetingText = '中午好'
  } else if (hour >= 14 && hour < 18) {
    greetingText = '下午好'
  } else if (hour >= 18 && hour < 22) {
    greetingText = '晚上好'
  } else {
    greetingText = '夜深了'
  }

  return `${greetingText}！${currentUser.value}`
})

const activeTaskCount = computed(() => activeCountRef.value || 0)

const openTaskCenter = () => {
  taskerStore.openDrawer()
}

const openSettings = () => {
  openSettingsModal?.(userStore.isAdmin ? 'base' : 'account')
}

const toggleTheme = () => {
  themeStore.toggleTheme()
}

// 更新时间
const updateTime = () => {
  const now = dayjs().tz('Asia/Shanghai')
  currentTime.value = now.format('YYYY年MM月DD日 HH:mm:ss')
}

// 定时器
let timeInterval = null

onMounted(async () => {
  updateTime()
  timeInterval = setInterval(updateTime, 1000)

  // 获取用户信息
  try {
    await userStore.getCurrentUser()
  } catch (error) {
    console.error('获取用户信息失败:', error)
  }
})

onUnmounted(() => {
  if (timeInterval) {
    clearInterval(timeInterval)
  }
})
</script>

<style scoped lang="less">
.status-bar {
  display: flex;
  align-items: center;
  top: 0;
  z-index: 100;
}

.status-bar-content {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px var(--page-padding);
}

.status-left {
  display: flex;
  align-items: center;
}

.system-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.system-details {
  .system-name {
    font-size: 20px;
    font-weight: 600;
    color: var(--gray-900, #111827);
    line-height: 1.4;
  }

  .system-subtitle {
    font-size: 13px;
    color: var(--gray-600, #6b7280);
    line-height: 1.2;
  }
}

.status-right {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 13px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-action-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  min-width: 32px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background-color: transparent;
  color: var(--gray-600, #4b5563);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease;

  &:hover,
  &.active {
    border-color: var(--gray-150, #e5e7eb);
    background: var(--gray-0, #fff);
    color: var(--gray-900, #111827);
  }

  .icon {
    width: 16px;
    height: 16px;
    color: inherit;
  }
}

.task-center-button {
  padding-right: 10px;
}

.task-center-label {
  line-height: 1;
}

.task-center-badge :deep(.ant-badge-count) {
  background-color: var(--main-color, #1d4ed8);
  box-shadow: 0 0 0 1px var(--gray-25, #f9fafb);
}

.time-info,
.user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  line-height: 1.3;
  color: var(--gray-600, #4b5563);

  .icon {
    width: 15px;
    height: 15px;
    color: var(--gray-600, #6b7280);
  }
}

.current-time,
.user-greeting {
  font-weight: 500;
  color: var(--gray-900, #111827);
}

// 响应式设计
@media (max-width: 768px) {
  .status-bar {
    height: 44px;
  }

  .status-bar-content {
    padding: 0 16px;
  }

  .system-details {
    .system-name {
      font-size: 13px;
    }

    .system-subtitle {
      font-size: 10px;
    }
  }

  .status-right {
    gap: 8px;
  }

  .header-actions {
    gap: 2px;
  }

  .task-center-label {
    display: none;
  }

  .time-info,
  .user-info {
    font-size: 11px;

    .icon {
      width: 12px;
      height: 12px;
    }
  }

  .current-time {
    display: none; // 在小屏幕上隐藏时间
  }
}
</style>
