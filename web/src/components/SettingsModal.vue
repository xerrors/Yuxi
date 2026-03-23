<template>
  <a-modal
    v-model:open="visible"
    :title="null"
    width="90%"
    :style="{ maxWidth: '980px', minWidth: '320px', top: '10%' }"
    :footer="null"
    :closable="false"
    @cancel="handleClose"
    class="settings-modal"
    :destroyOnClose="true"
    :bodyStyle="{ padding: 0 }"
  >
    <div class="settings-container">
      <button class="settings-close-btn lucide-icon-btn" @click="handleClose" aria-label="关闭设置">
        <X :size="16" />
      </button>

      <!-- 侧边栏 (Desktop) -->
      <div class="settings-sider">
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'base' }"
          @click="activeTab = 'base'"
          v-if="userStore.isAdmin"
        >
          <Settings class="icon" :size="18" />
          <span>基本设置</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'model' }"
          @click="activeTab = 'model'"
          v-if="userStore.isSuperAdmin"
        >
          <SquareCode class="icon" :size="18" />
          <span>模型配置</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'user' }"
          @click="activeTab = 'user'"
          v-if="userStore.isAdmin"
        >
          <User class="icon" :size="18" />
          <span>用户管理</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'department' }"
          @click="activeTab = 'department'"
          v-if="userStore.isSuperAdmin"
        >
          <Users class="icon" :size="18" />
          <span>部门管理</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'apikey' }"
          @click="activeTab = 'apikey'"
          v-if="userStore.isSuperAdmin"
        >
          <KeyIcon class="icon" :size="18" />
          <span>API Key</span>
        </div>
      </div>

      <!-- 顶部导航 (Mobile) -->
      <div class="settings-mobile-nav">
        <div
          class="nav-item"
          :class="{ active: activeTab === 'base' }"
          @click="activeTab = 'base'"
          v-if="userStore.isAdmin"
        >
          基本设置
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'model' }"
          @click="activeTab = 'model'"
          v-if="userStore.isSuperAdmin"
        >
          模型配置
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'user' }"
          @click="activeTab = 'user'"
          v-if="userStore.isAdmin"
        >
          用户管理
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'department' }"
          @click="activeTab = 'department'"
          v-if="userStore.isSuperAdmin"
        >
          部门管理
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'apikey' }"
          @click="activeTab = 'apikey'"
          v-if="userStore.isSuperAdmin"
        >
          API Key
        </div>
      </div>

      <!-- 内容区域 -->
      <div class="settings-content-wrapper">
        <div class="settings-content">
          <div v-show="activeTab === 'base'" v-if="userStore.isAdmin">
            <BasicSettingsSection />
          </div>

          <div v-show="activeTab === 'model'" v-if="userStore.isSuperAdmin">
            <ModelProvidersComponent />
          </div>

          <div v-show="activeTab === 'user'" v-if="userStore.isAdmin">
            <UserManagementComponent />
          </div>

          <div v-show="activeTab === 'department'" v-if="userStore.isSuperAdmin">
            <DepartmentManagementComponent />
          </div>

          <div v-show="activeTab === 'apikey'" v-if="userStore.isSuperAdmin">
            <ApiKeyManagementComponent />
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useUserStore } from '@/stores/user'
import { Key as KeyIcon, Settings, SquareCode, User, Users, X } from 'lucide-vue-next'
import BasicSettingsSection from '@/components/BasicSettingsSection.vue'
import ModelProvidersComponent from '@/components/ModelProvidersComponent.vue'
import UserManagementComponent from '@/components/UserManagementComponent.vue'
import DepartmentManagementComponent from '@/components/DepartmentManagementComponent.vue'
import ApiKeyManagementComponent from '@/components/ApiKeyManagementComponent.vue'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:visible', 'close'])

const userStore = useUserStore()
const activeTab = ref('base')

const visible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value)
})

const handleClose = () => {
  emit('close')
}

// 根据用户权限设置默认标签页
watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      if (userStore.isAdmin) {
        activeTab.value = 'base'
      }
    }
  }
)
</script>

<style lang="less">
.settings-modal.ant-modal {
  .ant-modal-content {
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    position: relative;
    padding: 0;
    overflow: hidden;
  }

  .ant-modal-body {
    padding: 0;
  }
}

.settings-container {
  display: flex;
  height: 70vh;
  width: 100%;
  position: relative;

  @media (max-width: 900px) {
    flex-direction: column;
    height: auto;
    min-height: 70vh;
  }
}

.settings-close-btn {
  position: absolute;
  top: 10px;
  left: 14px;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--gray-700);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 2;

  &:hover {
    background: var(--gray-200);
    color: var(--gray-900);
  }
}

/* Sidebar Styles - Matching SettingView.vue style */
.settings-sider {
  width: 176px;
  height: 100%;
  padding: 52px 10px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  background: var(--gray-50);
  border-right: 1px solid var(--gray-150);

  @media (max-width: 900px) {
    display: none;
  }

  .sider-item {
    width: 100%;
    padding: 6px 12px; /* Matches SettingView .sider > * */
    cursor: pointer;
    transition: all 0.1s; /* Matches SettingView */
    text-align: left;
    font-size: 15px; /* Matches SettingView */
    border-radius: 8px;
    color: var(--gray-700);
    display: flex;
    align-items: center;
    gap: 10px;

    .icon {
      font-size: 14px; /* Slightly adjusted to align better, SettingView uses h() icon defaults */
    }

    &:hover {
      background: var(--gray-50);
    }

    &.activesec {
      background: var(--gray-150);
      color: var(--main-700);
    }
  }
}

/* Content Area */
.settings-content-wrapper {
  flex: 1;
  height: 100%;
  max-width: calc(100% - 176px);
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
  padding: 0;

  @media (max-width: 900px) {
    max-width: 100%;
    padding: 8px;
  }

  .settings-content {
    padding: 12px 16px 16px; /* Keep inner readability without outer panel padding */
    // margin-bottom: 40px; /* Matches SettingView .setting margin-bottom */
    overflow-y: scroll;
    height: auto;
    flex: 1;
    min-height: 0;

    .model-providers-section,
    .user-management,
    .department-management,
    .apikey-management {
      min-height: auto;
    }

    .header-section {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      margin-bottom: 16px;
    }

    .header-content {
      flex: 1;
      min-width: 0;
    }

    .section-title {
      font-size: 16px;
      font-weight: 500;
      color: var(--gray-900);
      line-height: 1.4;
      margin: 12px 0 4px;
    }

    .section-description {
      font-size: 14px;
      color: var(--gray-600);
      line-height: 1.4;
      margin: 0;
    }

    .section-subtitle {
      margin: 0;
      font-size: 16px;
      font-weight: 500;
      color: var(--gray-900);
    }

    .add-btn {
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    @media (max-width: 900px) {
      height: auto;
      padding: 10px 12px 12px;
    }
  }
}

/* Mobile Styles */
.settings-mobile-nav {
  display: none;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  padding: 0;
  padding-left: 42px;
  flex-shrink: 0;

  @media (max-width: 900px) {
    display: flex;
  }

  .nav-item {
    padding: 12px 16px;
    white-space: nowrap;
    cursor: pointer;
    color: var(--gray-600);
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;

    &.active {
      color: var(--main-color);
      border-bottom-color: var(--main-color);
    }
  }
}
</style>
