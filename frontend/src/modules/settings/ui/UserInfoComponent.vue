<template>
  <div class="user-info-component">
    <a-dropdown
      v-model:open="menuOpen"
      :trigger="['click']"
      placement="topLeft"
      :align="{ offset: [0, -8] }"
      v-if="userStore.isLoggedIn"
    >
      <div class="user-info-dropdown" :class="{ 'is-open': menuOpen }" :data-align="showRole ? 'left' : 'center'">
        <div class="user-avatar">
          <FallbackAvatar
            :src="userStore.avatar"
            :name="userStore.displayName"
            :seed="userStore.uid || userStore.username"
            kind="user"
            :size="32"
            shape="circle"
            :alt="userStore.displayName"
            class="avatar-image"
          />
          <!-- <div class="user-role-badge" :class="userRoleClass"></div> -->
        </div>
        <div v-if="showRole" class="user-name" :title="userStore.displayName">
          {{ userStore.displayName }}
        </div>
        <div v-if="slots.actions" class="user-info-actions">
          <slot name="actions" />
        </div>
      </div>
      <template #overlay>
        <a-menu class="user-account-menu" @click="handleMenuClick">
          <a-menu-item key="user-info" @click="openProfile">
            <div class="user-info-display">
              <div class="user-menu-username" :title="userStore.displayName">
                {{ userStore.displayName }}
              </div>
              <div class="user-menu-details">
                <span
                  class="user-menu-info"
                  :title="userStore.isEnterpriseAccount ? '企业账号' : userStore.uid"
                >
                  {{ userStore.isEnterpriseAccount ? '企业账号' : `ID: ${userStore.uid}` }}
                </span>
                <span class="user-menu-separator" aria-hidden="true">·</span>
                <span class="user-menu-role">{{ userRoleText }}</span>
              </div>
            </div>
          </a-menu-item>
          <a-menu-divider />
          <a-menu-item key="docs" @click="openDocs">
            <template #icon><BookOpen :size="16" /></template>
            <span class="menu-text">文档中心</span>
          </a-menu-item>
          <a-menu-item key="github" @click="openGitHub">
            <template #icon>
              <svg
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path
                  d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0c-2.4-1.6-3.5-1.3-3.5-1.3a4.2 4.2 0 0 0-.1 3.2 4.6 4.6 0 0 0-1.3 3.2c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2v3.5"
                />
              </svg>
            </template>
            <span class="menu-text">给个 Star / 提个 Issue</span>
          </a-menu-item>
          <a-menu-item key="theme" @click="toggleTheme">
            <template #icon>
              <Sun v-if="themeStore.themeMode === 'light'" :size="16" />
              <Moon v-else-if="themeStore.themeMode === 'dark'" :size="16" />
              <Monitor v-else :size="16" />
            </template>
            <span class="menu-text">主题：{{ themeStore.themeLabel }}</span>
          </a-menu-item>
          <a-menu-divider />
          <a-menu-item v-if="userStore.isSuperAdmin" key="debug" @click="infoStore.openDebugModal">
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
    <DebugComponent v-model:show="infoStore.showDebugModal" />
  </div>
</template>

<script setup>
import { computed, inject, ref, useSlots } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '@/apis/auth_api'
import { useUserStore } from '@/modules/identity'
import { useInfoStore } from '@/modules/settings/model/info'
import DebugComponent from '@/modules/settings/ui/DebugComponent.vue'
import { message } from 'ant-design-vue'
import { BookOpen, Sun, Moon, Monitor, LogOut, Settings, Terminal } from '@lucide/vue'
import { useThemeStore } from '@/shared/model/theme'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'

const router = useRouter()
const userStore = useUserStore()
const infoStore = useInfoStore()
const themeStore = useThemeStore()
const slots = useSlots()
const menuOpen = ref(false)
/** 主题项允许连续轮换，其他菜单操作关闭浮层。 */
function handleMenuClick({ key }) {
  if (key !== 'theme') menuOpen.value = false
}

// Inject settings modal methods
const { openSettingsModal } = inject('settingsModal', {})

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
const logout = async () => {
  const requestToken = userStore.token
  try {
    await authApi.logout()
  } catch (error) {
    if (error.status !== 401) {
      if (userStore.token === requestToken) message.error(error.message || '退出未完成，请重试')
      return
    }
  }
  // 当前会话的 401 可能已由请求层清理，仍可以进入登录页。
  if (userStore.token && userStore.token !== requestToken) return
  if (userStore.token) userStore.logout()
  await router.replace('/login')
}

// 前往登录页
const goToLogin = () => {
  router.push('/login')
}

const openDocs = () => {
  window.open('https://xerrors.github.io/Yuxi/', '_blank', 'noopener,noreferrer')
}

const openGitHub = () => {
  window.open('https://github.com/xerrors/Yuxi', '_blank', 'noopener,noreferrer')
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

.user-account-menu {
  width: 260px;
  max-width: calc(100vw - 24px);
  border-radius: 8px;
}

.user-info-display {
  min-width: 0;
  width: 100%;
  padding: 4px 0;
  line-height: 1.4;
}

.user-menu-username {
  font-weight: 600;
  color: var(--gray-900);
  font-size: 14px;
  display: block;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-menu-details {
  display: flex;
  gap: 6px;
  align-items: center;
}

.user-menu-info {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--gray-500);
}

.user-menu-separator {
  color: var(--gray-400);
}

.user-menu-role {
  flex-shrink: 0;
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
  min-width: 0;
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
