<template>
  <DashboardChartCard title="用户活跃度分析" :loading="loading" class="dashboard-card">
    <template #summary>
      <div class="dashboard-card-metric-grid">
        <DashboardMetricCard
          :icon="Users"
          :value="formatStatNumber(userStats, userStats?.total_users)"
          label="总用户"
          tone="info"
          compact
        />
        <DashboardMetricCard
          :icon="Activity"
          :value="formatStatNumber(userStats, userStats?.active_users_24h)"
          label="24 小时活跃"
          tone="success"
          compact
        />
        <DashboardMetricCard
          :icon="CalendarDays"
          :value="formatStatNumber(userStats, userStats?.active_users_30d)"
          label="30 天活跃"
          tone="primary"
          compact
        />
      </div>
    </template>

    <div class="activity-heatmap-container">
      <div class="chart-header">
        <div>
          <span class="chart-title">活跃度分布</span>
          <span class="chart-subtitle">近 120 天</span>
        </div>
        <span class="chart-hint">按日统计活跃用户</span>
      </div>
      <DashboardActivityHeatmap :data="userStats?.daily_active_users" :loading="loading" />
    </div>
  </DashboardChartCard>
</template>

<script setup>
import DashboardChartCard from './DashboardChartCard.vue'
import { Activity, CalendarDays, Users } from '@lucide/vue'
import { formatStatNumber } from '@/modules/dashboard/model/dashboard'
import DashboardActivityHeatmap from './DashboardActivityHeatmap.vue'
import DashboardMetricCard from './DashboardMetricCard.vue'

defineProps({
  userStats: {
    type: Object,
    default: () => ({})
  },
  loading: {
    type: Boolean,
    default: false
  }
})

// 保留父页面的统一图表生命周期接口；活跃方格图不需要手动初始化 ECharts。
const updateCharts = () => {}
const cleanup = () => {}

defineExpose({
  updateCharts,
  cleanup
})
</script>

<style scoped lang="less">
.activity-heatmap-container {
  padding: 0 0 2px;
}

.chart-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.chart-title {
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
}

.chart-subtitle,
.chart-hint {
  color: var(--gray-500);
  font-size: 11px;
}

.chart-subtitle {
  margin-left: 8px;
}

@media (max-width: 560px) {
  .chart-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }
}
</style>
