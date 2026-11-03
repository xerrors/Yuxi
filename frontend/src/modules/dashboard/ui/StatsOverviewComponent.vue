<template>
  <div class="stats-overview-container">
    <DashboardMetricGrid>
      <DashboardMetricCard
        :icon="MessageCircle"
        :value="basicValue(props.basicStats?.total_sessions)"
        label="累计会话"
        tone="primary"
      >
        <template #meta v-if="props.basicStats?.session_trend">
          <span class="metric-trend" :class="props.basicStats.session_trend > 0 ? 'up' : 'down'">
            <TrendingUp v-if="props.basicStats.session_trend > 0" />
            <TrendingDown v-else />
            {{ Math.abs(props.basicStats.session_trend) }}%
          </span>
        </template>
      </DashboardMetricCard>

      <DashboardMetricCard
        :icon="Activity"
        :value="basicValue(props.basicStats?.active_sessions)"
        label="未归档会话"
        tone="success"
      />
      <DashboardMetricCard
        :icon="Mail"
        :value="basicValue(props.basicStats?.total_messages)"
        label="总消息数"
        tone="info"
      />
      <DashboardMetricCard
        :icon="Users"
        :value="basicValue(props.basicStats?.total_users)"
        label="用户数"
        tone="warning"
      />
      <DashboardMetricCard
        :icon="MonitorCheck"
        :value="vmValue(vmStats?.active_vms)"
        label="当前活跃 VM"
        title="执行节点当前运行中的 VM"
        tone="success"
      />
      <DashboardMetricCard
        :icon="Server"
        :value="vmValue(vmStats?.total_vms)"
        label="VM 总数"
        title="执行节点全部 VM，包含休眠实例"
        tone="accent"
      >
        <template #meta>
          <button
            type="button"
            class="vm-refresh"
            :disabled="vmStatsLoading"
            :aria-label="vmStatsError ? '重试 VM 统计' : '刷新 VM 统计'"
            @click="$emit('refresh-vms')"
          >
            <RefreshCw aria-hidden="true" />
          </button>
        </template>
      </DashboardMetricCard>
    </DashboardMetricGrid>
    <div v-if="props.basicStatsError" class="stats-error" role="status">
      <span>统计数据加载失败，当前不显示具体数值。</span>
      <button type="button" class="stats-retry" @click="$emit('refresh-basic')">重试</button>
    </div>
    <p v-if="vmStatsError" class="vm-stats-error" role="status">VM 统计暂不可用，请重试</p>
  </div>
</template>

<script setup>
import {
  MessageCircle,
  Activity,
  Mail,
  Users,
  MonitorCheck,
  Server,
  RefreshCw,
  TrendingUp,
  TrendingDown
} from '@lucide/vue'
import { formatNumber } from '@/modules/dashboard/model/dashboard'
import DashboardMetricCard from './DashboardMetricCard.vue'
import DashboardMetricGrid from './DashboardMetricGrid.vue'

const props = defineProps({
  basicStats: {
    type: Object,
    default: () => ({})
  },
  basicStatsLoading: {
    type: Boolean,
    default: false
  },
  basicStatsError: {
    type: Boolean,
    default: false
  },
  vmStats: {
    type: Object,
    default: null
  },
  vmStatsLoading: {
    type: Boolean,
    default: false
  },
  vmStatsError: {
    type: Boolean,
    default: false
  }
})
defineEmits(['refresh-vms', 'refresh-basic'])

/** 基础统计未就绪或失败时保留未知值，避免把加载失败展示为零。 */
const basicValue = (value) => {
  if (props.basicStatsLoading) return '…'
  if (props.basicStatsError) return '—'
  return value == null ? '—' : formatNumber(value)
}

/** 未完成读取或失败时保留未知值，避免展示为零。 */
const vmValue = (value) => {
  if (props.vmStatsLoading) return '…'
  return value == null ? '—' : formatNumber(value)
}
</script>

<style lang="less" scoped>
.stats-overview-container {
  margin-top: 8px;
}

.dashboard-metric-grid {
  padding: 0 var(--page-padding);
  grid-template-columns: repeat(6, minmax(0, 1fr));
}

@media (max-width: 1400px) {
  .dashboard-metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .dashboard-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.vm-refresh {
  display: inline-flex;
  width: 24px;
  height: 24px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;

  svg {
    width: 12px;
    height: 12px;
  }

  &:hover {
    color: var(--main-color);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
  }
}

.vm-stats-error {
  margin: 8px var(--page-padding) 0;
  color: var(--color-error-700);
  font-size: 12px;
}

.stats-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px var(--page-padding) 0;
  color: var(--color-error-700);
  font-size: 12px;

  .stats-retry {
    padding: 0;
    border: 0;
    background: none;
    color: var(--main-color);
    font-size: 12px;
    cursor: pointer;
    text-decoration: underline;
  }
}

.metric-trend {
  display: inline-flex;
  align-items: center;
  gap: 2px;

  svg {
    width: 12px;
    height: 12px;
  }

  &.up {
    color: var(--color-success-700);
  }

  &.down {
    color: var(--color-error-700);
  }
}
</style>
