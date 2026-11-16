<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '@/shared/ui/PageHeader.vue'
import ApiIntegrationPanel from '@/modules/settings/ui/ApiIntegrationPanel.vue'
import ModelProviderManagePanel from '@/modules/settings/ui/ModelProviderManagePanel.vue'
import { useUserStore } from '@/modules/identity/model/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const activeTab = ref('providers')
const providerRef = ref(null)
const apiRef = ref(null)

const tabs = computed(() => {
  const items = [{ key: 'providers', label: '模型供应商' }]
  if (userStore.isSuperAdmin) items.push({ key: 'api', label: 'API集成' })
  return items
})

const activePanel = computed(() => activeTab.value === 'api' ? apiRef.value : providerRef.value)
const loading = computed(() => activePanel.value?.loading || activePanel.value?.saving || false)
const stats = computed(() => activePanel.value?.stats || {})

function normalizeTab(tab) {
  if (tab === 'api' && userStore.isSuperAdmin) return 'api'
  return 'providers'
}

watch(
  () => [route.query.tab, userStore.isSuperAdmin],
  ([tab]) => {
    const next = normalizeTab(tab)
    if (activeTab.value !== next) activeTab.value = next
  },
  { immediate: true }
)

async function requestTabChange(item) {
  const next = normalizeTab(item.key)
  if (next === activeTab.value) return
  await router.replace({ query: { ...route.query, tab: next } })
}
</script>

<template>
  <div class="api-services-view">
    <PageHeader
      title="API服务"
      :active-key="activeTab"
      :tabs="tabs"
      :loading="loading"
      :show-border="true"
      aria-label="API服务功能切换"
      @change="requestTabChange"
    >
      <template #info>
        <div v-if="activeTab === 'providers'" class="summary-strip">
          <span>{{ stats.total || 0 }} 个供应商</span>
          <span>{{ stats.enabled || 0 }} 个启用</span>
          <span v-if="stats.warning > 0" class="warning-count">
            {{ stats.warning }} 个凭证缺失
          </span>
          <span>{{ stats.models || 0 }} 个模型</span>
        </div>
        <div v-else class="summary-strip">
          <span>{{ stats.app_count || 0 }} 个接入 APP</span>
          <span>{{ stats.key_count || 0 }} 个有效 API Key</span>
        </div>
      </template>
    </PageHeader>

    <div class="api-services-content">
      <ModelProviderManagePanel v-if="activeTab === 'providers'" ref="providerRef" />
      <ApiIntegrationPanel v-else-if="userStore.isSuperAdmin" ref="apiRef" />
    </div>
  </div>
</template>

<style scoped lang="less">
.api-services-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.api-services-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  span {
    padding: 6px 10px;
    border: 1px solid var(--gray-100);
    border-radius: 7px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
  }

  .warning-count {
    background: var(--color-warning-50);
    border-color: var(--color-warning-100);
    color: var(--color-warning-700);
  }
}
</style>
