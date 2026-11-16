<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '@/shared/ui/PageHeader.vue'
import AgentManagePanel from '@/modules/agents/ui/AgentManagePanel.vue'

const route = useRoute()
const agentPanelRef = ref(null)
const loading = computed(() => agentPanelRef.value?.loading || agentPanelRef.value?.saving || false)
const stats = computed(() => agentPanelRef.value?.stats || {})
</script>

<template>
  <div class="agent-manage-view">
    <PageHeader title="智能体管理" :loading="loading" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>{{ stats.total || 0 }} 个智能体</span>
          <span>{{ stats.manageable || 0 }} 个可编辑</span>
          <span v-if="!stats.manageable">可新建私有智能体</span>
        </div>
      </template>
    </PageHeader>

    <div class="agent-manage-content">
      <AgentManagePanel ref="agentPanelRef" :focus-agent="String(route.query.agent || '')" />
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
