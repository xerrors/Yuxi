<template>
  <div class="dashboard-container">
    <StatsOverviewComponent
      :basic-stats="basicStats"
      :basic-stats-loading="loading"
      :basic-stats-error="basicStatsError"
      :vm-stats="vmStats"
      :vm-stats-loading="vmStatsLoading"
      :vm-stats-error="vmStatsError"
      @refresh-vms="loadVmStats"
      @refresh-basic="retryBasicStats"
    />

    <div class="dashboard-grid">
      <CallStatsComponent />
      <div class="grid-item user-stats">
        <UserStatsComponent
          :user-stats="allStatsData?.users"
          :loading="loading"
        />
      </div>
      <ConversationDistributionCard />
      <div class="grid-item tool-stats">
        <ToolStatsComponent
          :tool-stats="allStatsData?.tools"
          :loading="loading"
        />
      </div>
      <div class="grid-item knowledge-stats">
        <KnowledgeStatsComponent
          :knowledge-stats="allStatsData?.knowledge"
          :loading="loading"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onActivated } from 'vue'
import { message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'

// 导入子组件
import UserStatsComponent from '@/modules/dashboard/ui/UserStatsComponent.vue'
import ToolStatsComponent from '@/modules/dashboard/ui/ToolStatsComponent.vue'
import KnowledgeStatsComponent from '@/modules/dashboard/ui/KnowledgeStatsComponent.vue'
import CallStatsComponent from '@/modules/dashboard/ui/CallStatsComponent.vue'
import StatsOverviewComponent from '@/modules/dashboard/ui/StatsOverviewComponent.vue'
import ConversationDistributionCard from '@/modules/dashboard/ui/ConversationDistributionCard.vue'

// 统计数据
const basicStats = ref({})
const basicStatsError = ref(false)
const vmStats = ref(null)
const vmStatsLoading = ref(false)
const vmStatsError = ref(false)
const allStatsData = ref({
  users: null,
  tools: null,
  knowledge: null
})

const loading = ref(true)
const overviewLoaded = ref(false)

/** 独立读取执行节点计数，控制面不可用时保留其他统计。 */
const loadVmStats = async () => {
  if (vmStatsLoading.value) return
  vmStatsLoading.value = true
  vmStatsError.value = false
  try {
    vmStats.value = await dashboardApi.getVmStats()
  } catch {
    vmStats.value = null
    vmStatsError.value = true
  } finally {
    vmStatsLoading.value = false
  }
}

// 加载概览统计数据
const loadAllStats = async () => {
  if (overviewLoaded.value) return
  loading.value = true
  basicStatsError.value = false
  try {
    const response = await dashboardApi.getAllStats()
    allStatsData.value = {
      users: response.users,
      tools: response.tools,
      knowledge: response.knowledge
    }
    if (!response.basic) {
      // 基础指标失败：卡片保持错误态，不把失败投影为 0。
      basicStats.value = {}
      basicStatsError.value = true
      message.error('无法加载统计数据')
    } else {
      basicStats.value = response.basic
      overviewLoaded.value = true
      const missingDetails = ['users', 'tools', 'knowledge'].filter((key) => !response[key])
      if (missingDetails.length === 3) {
        message.warning('详细统计暂不可用，当前仅显示基础指标')
      } else if (missingDetails.length > 0) {
        message.warning('部分详细统计暂不可用')
      }
    }
  } catch (error) {
    console.error('加载统计数据失败:', error)
    basicStats.value = {}
    basicStatsError.value = true
    message.error('无法加载统计数据')
  } finally {
    loading.value = false
  }
}

/** 错误态下重试基础统计。 */
const retryBasicStats = async () => {
  if (loading.value) return
  overviewLoaded.value = false
  await loadAllStats()
}

onActivated(() => Promise.all([loadAllStats(), loadVmStats()]))
defineExpose({ loading })
</script>

<style scoped lang="less">
.dashboard-container {
  background-color: var(--gray-25);
  min-height: calc(100vh - 64px);
  overflow-x: hidden;
}

:global(:root.dark .dashboard-container) {
  background-color: var(--bg-page);
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 16px;
  padding: var(--page-padding);
  align-items: stretch;
}
.dashboard-grid > * {
  min-width: 0;
  grid-column: span 4;
}
.dashboard-grid > .call-stats {
  grid-column: span 8;
}
.grid-item {
  min-width: 0;
}
.dashboard-grid :deep(.ant-card-head-title) {
  font-size: 15px !important;
}
.dashboard-grid :deep(.ant-card-head) {
  min-height: 52px !important;
  padding-inline: 18px !important;
}
.dashboard-grid :deep(.ant-card-body) {
  padding: 0 18px 18px !important;
}
.dashboard-grid :deep(.dashboard-card-metric-grid) {
  gap: 8px;
}
.dashboard-grid :deep(.dashboard-card-metric-grid .dashboard-metric-icon) {
  display: none;
}
.dashboard-grid :deep(.dashboard-card-metric-grid .dashboard-metric-label) {
  font-size: 11px;
}
.dashboard-grid :deep(.dashboard-card-metric-grid .dashboard-metric-value) {
  font-size: 22px;
}
@media (max-width: 1200px) {
  .dashboard-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .dashboard-grid > *,
  .dashboard-grid > .call-stats {
    grid-column: span 1;
  }
}
@media (max-width: 900px) {
  .dashboard-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
}
</style>
