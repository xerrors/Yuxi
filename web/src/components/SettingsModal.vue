<template>
  <a-modal
    v-model:open="visible"
    title="系统设置"
    width="90%"
    :style="{ maxWidth: '980px', minWidth: '320px', top: '10%' }"
    :footer="null"
    @cancel="handleClose"
    class="settings-modal"
    :destroyOnClose="true"
    :bodyStyle="{ padding: 0 }"
  >
    <div class="settings-container">
      <!-- 侧边栏 (Desktop) -->
      <div class="settings-sider">
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'base' }"
          @click="activeTab = 'base'"
          v-if="userStore.isSuperAdmin"
        >
          <SettingOutlined class="icon" />
          <span>基本设置</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'model' }"
          @click="activeTab = 'model'"
          v-if="userStore.isSuperAdmin"
        >
          <CodeOutlined class="icon" />
          <span>模型配置</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'user' }"
          @click="activeTab = 'user'"
          v-if="userStore.isAdmin"
        >
          <UserOutlined class="icon" />
          <span>用户管理</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'department' }"
          @click="activeTab = 'department'"
          v-if="userStore.isSuperAdmin"
        >
          <TeamOutlined class="icon" />
          <span>部门管理</span>
        </div>
        <div
          class="sider-item"
          :class="{ activesec: activeTab === 'apikey' }"
          @click="activeTab = 'apikey'"
          v-if="userStore.isSuperAdmin"
        >
          <KeyIcon class="icon" :size="14" />
          <span>API Key</span>
        </div>
      </div>

      <!-- 顶部导航 (Mobile) -->
      <div class="settings-mobile-nav">
        <div
          class="nav-item"
          :class="{ active: activeTab === 'base' }"
          @click="activeTab = 'base'"
          v-if="userStore.isSuperAdmin"
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
          <div v-show="activeTab === 'base'" v-if="userStore.isSuperAdmin">
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
import { SettingOutlined, CodeOutlined, UserOutlined, TeamOutlined } from '@ant-design/icons-vue'
import { Key as KeyIcon } from 'lucide-vue-next'
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
      if (userStore.isSuperAdmin) {
        activeTab.value = 'base'
      } else if (userStore.isAdmin) {
        activeTab.value = 'user'
      }
    }
  }
)
</script>

<style lang="less">
.settings-modal {
  :deep(.ant-modal-header) {
    padding: 16px 24px;
    border-bottom: 1px solid var(--gray-150);

    .ant-modal-title {
      font-size: 18px;
      font-weight: 600;
      color: var(--gray-900);
    }
  }

  :deep(.ant-modal-content) {
    border-radius: 8px;
    display: flex;
    flex-direction: column;
  }
}

.settings-container {
  display: flex;
  height: 100%;
  width: 100%;
  gap: 6px;

  @media (max-width: 900px) {
    flex-direction: column;
  }
}

/* Sidebar Styles - Matching SettingView.vue style */
.settings-sider {
  width: 128px;
  height: 100%;
  padding-top: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;

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
    margin-left: -20px;

    .icon {
      font-size: 14px; /* Slightly adjusted to align better, SettingView uses h() icon defaults */
    }

    &:hover {
      background: var(--gray-50);
    }

    &.activesec {
      background: var(--gray-100);
      color: var(--main-700);
    }
  }
}

/* Content Area */
.settings-content-wrapper {
  flex: 1;
  height: 100%;
  max-width: calc(100% - 128px);

  @media (max-width: 900px) {
    max-width: 100%;
  }

  .settings-content {
    padding: 0; /* Matches SettingView .setting padding */
    // margin-bottom: 40px; /* Matches SettingView .setting margin-bottom */
    overflow-y: scroll;
    height: 70vh;

    @media (max-width: 900px) {
      height: 70vh;
      padding: 0px;
    }

    h3 {
      font-weight: 600;
      color: var(--gray-900);
      margin-bottom: 0.5em;
    }

    /* BasicSettingsSection has its own h3 styles which might conflict slightly but are mostly self-contained */
  }
}

/* Mobile Styles */
.settings-mobile-nav {
  display: none;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  padding: 0;
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
