<script setup>
import { computed, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import PageHeader from '@/shared/ui/PageHeader.vue'
import AgentManagePanel from '@/modules/agents/ui/AgentManagePanel.vue'
import ScheduledAgentsView from '@/modules/agents/ui/ScheduledAgents.vue'

const route = useRoute()
const router = useRouter()

const activeTab = ref('agents')
const agentPanelRef = ref(null)
const schedulePanelRef = ref(null)

const tabs = [
  { key: 'agents', label: '智能体' },
  { key: 'schedules', label: '定时任务' }
]

const activePanel = computed(() => {
  if (activeTab.value === 'schedules') return schedulePanelRef.value
  return agentPanelRef.value
})

const activeLoading = computed(
  () => activePanel.value?.loading || activePanel.value?.saving || false
)
const activeStats = computed(() => activePanel.value?.stats || {})

const normalizeTab = (tab) => tab === 'schedules' ? 'schedules' : 'agents'

watch(
  () => route.query.tab,
  (tab) => { activeTab.value = normalizeTab(tab) },
  { immediate: true }
)

function canChangeTab(nextTab) {
  if (activeTab.value !== 'schedules' || nextTab === 'schedules') return true
  return schedulePanelRef.value?.beforeLeave?.() ?? true
}

async function requestTabChange(item) {
  const nextTab = normalizeTab(item.key)
  if (nextTab === activeTab.value) return
  await router.replace({ query: { ...route.query, tab: nextTab } })
}

onBeforeRouteUpdate((to) => canChangeTab(normalizeTab(to.query.tab)))
</script>

<template>
  <div class="agent-manage-view">
    <PageHeader
      :active-key="activeTab"
      title="智能体管理"
      :tabs="tabs"
      :loading="activeLoading"
      :show-border="true"
      aria-label="智能体管理视图切换"
      @change="requestTabChange"
    >
      <template #info>
        <div v-if="activeTab === 'agents'" class="summary-strip">
          <span>{{ activeStats.total || 0 }} 个智能体</span>
          <span>{{ activeStats.manageable || 0 }} 个可编辑</span>
          <span v-if="!activeStats.manageable">可新建私有智能体</span>
        </div>
      </template>
    </PageHeader>

    <div class="agent-manage-content">
      <div v-show="activeTab === 'agents'" class="tab-panel">
        <AgentManagePanel ref="agentPanelRef" :focus-agent="String(route.query.agent || '')" />
      </div>
      <div v-if="activeTab === 'schedules'" class="tab-panel schedule-tab-panel">
        <ScheduledAgentsView ref="schedulePanelRef" />
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.agent-manage-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--bg-page);
  color: var(--gray-1000);
}

.agent-manage-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  .tab-panel {
    height: 100%;
    min-height: 0;
    overflow-y: auto;
  }

  .schedule-tab-panel {
    overflow: hidden;
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
}
</style>
