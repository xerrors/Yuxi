<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '@/shared/ui/PageHeader.vue'
import ModelProviderManagePanel from '@/modules/settings/ui/ModelProviderManagePanel.vue'
import ApiIntegrationPanel from '@/modules/settings/ui/ApiIntegrationPanel.vue'
import { useUserStore } from '@/modules/identity'
import { createAsyncPanel } from '@/shared/lib/asyncPanel'

const AgentFeedbackList = createAsyncPanel(
  () => import('@/modules/dashboard/ui/AgentFeedbackList.vue')
)
const DashboardView = createAsyncPanel(() => import('./DashboardView.vue'))
const ApiKeyManagementComponent = createAsyncPanel(
  () => import('@/modules/settings/ui/ApiKeyManagementComponent.vue')
)
const DepartmentManagementComponent = createAsyncPanel(
  () => import('@/modules/settings/ui/DepartmentManagementComponent.vue')
)
const UserManagementComponent = createAsyncPanel(
  () => import('@/modules/settings/ui/UserManagementComponent.vue')
)
const directoryPanels = {
  users: UserManagementComponent,
  departments: DepartmentManagementComponent
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const dashboardRef = ref(null)
const providerPanelRef = ref(null)
const apiIntegrationPanelRef = ref(null)

const tabs = computed(() => {
  const tabs = []
  if (userStore.isAdmin) tabs.push({ key: 'providers', label: '模型供应商' })
  tabs.push({ key: 'integration', label: 'API 集成' })
  if (userStore.isAdmin) tabs.push({ key: 'users', label: '用户管理' })
  if (userStore.isSuperAdmin) tabs.push({ key: 'departments', label: '部门管理' })
  if (userStore.isSuperAdmin) {
    tabs.push({ key: 'overview', label: '系统概览' }, { key: 'feedback', label: '智能体反馈' })
  }
  return tabs
})

const activeTab = computed(() => {
  const tab = route.query.tab
  const visibleTab = tabs.value.find((item) => item.key === tab)
  if (visibleTab) return visibleTab.key
  if (tab === 'departments' && userStore.isAdmin) return 'users'
  return tabs.value[0].key
})

const loading = computed(() => providerPanelRef.value?.loading || apiIntegrationPanelRef.value?.loading || dashboardRef.value?.loading || false)
const providerStats = computed(() => providerPanelRef.value?.stats || {})

/** 将当前管理标签写入链接。 */
function requestTabChange({ key }) {
  if (key === activeTab.value) return
  return router.replace({ query: { ...route.query, tab: key }, hash: route.hash })
}
</script>

<template>
  <div class="system-manage-view">
    <PageHeader
      :active-key="activeTab"
      title="系统管理"
      :tabs="tabs"
      :loading="loading"
      :show-border="true"
      aria-label="系统管理视图切换"
      @change="requestTabChange"
    >
      <template #info>
        <div v-if="activeTab === 'providers'" class="summary-strip">
          <span>{{ providerStats.enabled || 0 }} 个启用</span>
          <span v-if="providerStats.warning > 0" class="warning-count">
            {{ providerStats.warning }} 个凭证缺失
          </span>
          <span>{{ providerStats.models || 0 }} 个模型</span>
        </div>
      </template>
    </PageHeader>

    <div class="system-manage-content">
      <div v-if="userStore.isSuperAdmin" v-show="activeTab === 'overview'" class="tab-panel">
        <KeepAlive>
          <DashboardView v-if="activeTab === 'overview'" ref="dashboardRef" />
        </KeepAlive>
      </div>
      <div v-if="activeTab === 'feedback' && userStore.isSuperAdmin" class="tab-panel">
        <AgentFeedbackList />
      </div>
      <div v-if="activeTab === 'providers'" class="tab-panel">
        <ModelProviderManagePanel ref="providerPanelRef" />
      </div>
      <div v-if="activeTab === 'integration'" class="tab-panel settings-tab-panel">
        <ApiIntegrationPanel v-if="userStore.isSuperAdmin" ref="apiIntegrationPanelRef" />
        <ApiKeyManagementComponent @changed="apiIntegrationPanelRef?.load()" />
      </div>
      <div v-if="directoryPanels[activeTab]" class="tab-panel settings-tab-panel">
        <KeepAlive :key="userStore.userRole">
          <component :is="directoryPanels[activeTab]" />
        </KeepAlive>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.system-manage-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--bg-page);
  color: var(--gray-1000);
}

.system-manage-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  .tab-panel {
    height: 100%;
    min-height: 0;
    overflow-y: auto;
  }

  .settings-tab-panel {
    display: flex;
    flex-direction: column;
    gap: 24px;
    padding: 16px var(--page-padding) 24px;
  }
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;

  span {
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
    white-space: nowrap;
  }

  .warning-count {
    padding: 2px 8px;
    border-radius: 4px;
    background: var(--color-warning-50);
    color: var(--color-warning-700);
  }
}
</style>
