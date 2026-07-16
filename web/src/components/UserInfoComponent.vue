<template>
  <div class="user-info-component">
    <a-dropdown :trigger="['click']" v-if="userStore.isLoggedIn">
      <div class="user-info-dropdown" :data-align="showRole ? 'left' : 'center'">
        <div class="user-avatar">
          <FallbackAvatar
            :src="userStore.avatar"
            :default-src="avatarDefaultSrc"
            :name="userStore.username"
            :seed="userStore.uid || userStore.username"
            kind="user"
            :size="32"
            shape="circle"
            :alt="userStore.username"
            class="avatar-image"
          />
          <!-- <div class="user-role-badge" :class="userRoleClass"></div> -->
        </div>
        <div v-if="showRole" class="user-name">{{ userStore.username }}</div>
        <div v-if="slots.actions" class="user-info-actions">
          <slot name="actions" />
        </div>
      </div>
      <template #overlay>
        <a-menu>
          <a-menu-item key="user-info" @click="openProfile">
            <div class="user-info-display">
              <div class="user-menu-username">{{ userStore.username }}</div>
              <div class="user-menu-details">
                <span class="user-menu-info">ID: {{ userStore.uid }}</span>
                <span class="user-menu-role">{{ userRoleText }}</span>
              </div>
            </div>
          </a-menu-item>
          <a-menu-divider />
          <a-menu-item key="docs" @click="openDocs">
            <template #icon><BookOpen :size="16" /></template>
            <span class="menu-text">文档中心</span>
          </a-menu-item>
          <a-menu-item key="theme" @click="toggleTheme">
            <template #icon>
              <Sun v-if="themeStore.isDark" :size="16" />
              <Moon v-else :size="16" />
            </template>
            <span class="menu-text">{{
              themeStore.isDark ? '切换到浅色模式' : '切换到深色模式'
            }}</span>
          </a-menu-item>
          <a-menu-divider />
          <a-menu-item v-if="userStore.isSuperAdmin" key="debug" @click="showDebug = true">
            <template #icon><Terminal :size="16" /></template>
            <span class="menu-text">调试面板（非生产环境）</span>
          </a-menu-item>
          <a-menu-item key="setting" @click="goToSetting">
            <template #icon><Settings :size="16" /></template>
            <span class="menu-text">设置</span>
          </a-menu-item>
          <a-menu-item key="logout" @click="logout">
            <template #icon><LogOut :size="16" /></template>
            <span class="menu-text">退出登录</span>
          </a-menu-item>
        </a-menu>
      </template>
    </a-dropdown>
    <a-button v-else-if="showButton" type="primary" @click="goToLogin"> 登录 </a-button>

    <!-- 调试面板 Modal -->
    <DebugComponent v-model:show="showDebug" />
  </div>
</template>

<script setup>
import { computed, ref, inject, useSlots } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import DebugComponent from '@/components/DebugComponent.vue'
import { message } from 'ant-design-vue'
import { BookOpen, Sun, Moon, LogOut, Settings, Terminal } from 'lucide-vue-next'
import { useThemeStore } from '@/stores/theme'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'

const router = useRouter()
const userStore = useUserStore()
const themeStore = useThemeStore()
const slots = useSlots()

// 调试面板状态
const showDebug = ref(false)

// Inject settings modal methods
const { openSettingsModal } = inject('settingsModal', {})

const avatarDefaultSrc = computed(() => (userStore.uid ? generatePixelAvatar(userStore.uid) : ''))

defineProps({
  showRole: {
    type: Boolean,
    default: false
  },
  showButton: {
    type: Boolean,
    default: false
  }
})

// 用户角色显示文本
const userRoleText = computed(() => {
  switch (userStore.userRole) {
    case 'superadmin':
      return '超级管理员'
    case 'admin':
      return '管理员'
    case 'user':
      return '普通用户'
    default:
      return '未知角色'
  }
})

// 退出登录
const logout = () => {
  userStore.logout()
  message.success('已退出登录')
  // 跳转到首页
  router.push('/login')
}

// 前往登录页
const goToLogin = () => {
  router.push('/login')
}

const openDocs = () => {
  window.open('https://xerrors.github.io/Yuxi/', '_blank', 'noopener,noreferrer')
}

const toggleTheme = () => {
  themeStore.toggleTheme()
}

// 前往设置页
const goToSetting = () => {
  if (openSettingsModal) {
    openSettingsModal('account')
  }
}

const openProfile = () => {
  if (openSettingsModal) {
    openSettingsModal('account')
  }
}
</script>

<style lang="less" scoped>
.user-info-component {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--gray-800);
  font-family:
    -apple-system, BlinkMacSystemFont, 'Noto Sans SC', 'Roboto', 'HarmonyOS Sans SC', 'Segoe UI',
    'Helvetica Neue', Arial, sans-serif;
}

.user-info-dropdown {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;

  &[data-align='center'] {
    justify-content: center;
  }

  &[data-align='left'] {
    justify-content: flex-start;
  }
}

.user-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-info-actions {
  display: inline-flex;
  align-items: center;
  margin-left: auto;
}

.user-avatar {
  @avatar-size: 32px;
  width: @avatar-size;
  height: @avatar-size;
  min-width: @avatar-size;
  flex: 0 0 @avatar-size;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  font-size: 16px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  box-shadow: 0 2px 8px var(--shadow-1);

  &:hover {
    opacity: 0.9;
  }

  .avatar-image {
    width: 100%;
    height: 100%;
    display: block;
    box-sizing: border-box;
    object-fit: cover;
    border-radius: 50%;
    border: 2px solid var(--gray-150);
  }
}

.user-role-badge {
  position: absolute;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  right: 0;
  bottom: 0;
  border: 2px solid var(--gray-0);

  &.superadmin {
    background-color: var(--color-warning-500);
  }

  &.admin {
    background-color: var(--color-info-500); /* 蓝色，管理员 */
  }

  &.user {
    background-color: var(--color-success-500); /* 绿色，普通用户 */
  }
}

.user-info-display {
  line-height: 1.4;
}

.user-menu-username {
  font-weight: 600;
  color: var(--gray-900);
  font-size: 14px;
  display: block;
  margin-bottom: 2px;
}

.user-menu-details {
  display: flex;
  gap: 12px;
  align-items: center;
}

.user-menu-info {
  font-size: 12px;
  color: var(--gray-600);
}

.user-menu-role {
  font-size: 12px;
  color: var(--gray-500);
}

.login-icon {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: 50%;
  transition:
    background-color 0.2s,
    color 0.2s;
  color: var(--gray-900);

  &:hover {
    background-color: var(--main-10);
    color: var(--main-color);
  }
}

:deep(.ant-dropdown-menu) {
  padding: 8px 0;
}

:deep(.ant-dropdown-menu-title-content) {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--gray-900);
}

:deep(.ant-dropdown-menu-item svg) {
  margin-right: 4px;
  color: var(--gray-900);
  vertical-align: middle;
}

.menu-text {
  line-height: 20px;
}
</style>
