<template>
  <DashboardChartCard title="工具调用监控" :loading="loading" class="dashboard-card">
    <!-- 工具调用概览 -->
    <template #summary>
      <div class="dashboard-card-metric-grid">
        <DashboardMetricCard
          :icon="Activity"
          :value="formatStatNumber(toolStats, toolStats?.total_calls)"
          label="总调用次数"
          tone="info"
          compact
        />
        <DashboardMetricCard
          :icon="CircleAlert"
          :value="formatStatNumber(toolStats, toolStats?.failed_calls)"
          label="失败调用"
          tone="warning"
          compact
        />
        <DashboardMetricCard
          :icon="BadgeCheck"
          :value="toolStats ? `${toolStats.success_rate || 0}%` : '—'"
          label="成功率"
          :tone="getSuccessTone()"
          compact
        />
      </div>
    </template>

    <!-- 最常用工具 -->
    <div class="chart-container">
      <h4>最常用工具 TOP 10</h4>
      <div ref="toolsChartRef" class="chart"></div>
    </div>
  </DashboardChartCard>
</template>

<script setup>
import DashboardChartCard from './DashboardChartCard.vue'
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from '@/modules/dashboard/model/dashboardCharts'
import { getColorByIndex } from '@/shared/lib/chartColors'
import { formatStatNumber } from '@/modules/dashboard/model/dashboard'
import { useThemeStore } from '@/shared/model/theme'
import { Activity, BadgeCheck, CircleAlert } from '@lucide/vue'
import DashboardMetricCard from './DashboardMetricCard.vue'

// CSS 变量解析工具函数
function getCSSVariable(variableName, element = document.documentElement) {
  return getComputedStyle(element).getPropertyValue(variableName).trim()
}

// theme store
const themeStore = useThemeStore()

// Props
const props = defineProps({
  toolStats: {
    type: Object,
    default: () => ({})
  },
  loading: {
    type: Boolean,
    default: false
  }
})

// Chart refs
const toolsChartRef = ref(null)
let toolsChart = null
let resizeObserver = null

const getSuccessTone = () => {
  const rate = Number(props.toolStats?.success_rate || 0)
  if (rate >= 90) return 'success'
  if (rate >= 70) return 'warning'
  return 'neutral'
}

// 初始化最常用工具图表
const initToolsChart = () => {
  // 如果已存在图表实例，先销毁
  if (toolsChart) {
    toolsChart.dispose()
    toolsChart = null
  }

  resizeObserver?.disconnect()
  if (!toolsChartRef.value) return
  toolsChart = echarts.init(toolsChartRef.value)
  resizeObserver = new ResizeObserver(() => toolsChart?.resize())
  resizeObserver.observe(toolsChartRef.value)

  const data = [...(props.toolStats?.most_used_tools || [])]
    .sort((a, b) => b.count - a.count)
    .slice(0, 10)
    .reverse()

  const option = {
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow'
      },
      backgroundColor: getCSSVariable('--gray-0'),
      borderColor: getCSSVariable('--gray-200'),
      borderWidth: 1,
      textStyle: {
        color: getCSSVariable('--gray-600')
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      top: '5%',
      containLabel: true
    },
    xAxis: {
      type: 'value',
      axisLine: {
        lineStyle: {
          color: getCSSVariable('--gray-200')
        }
      },
      axisLabel: {
        color: getCSSVariable('--gray-500')
      },
      splitLine: {
        lineStyle: {
          color: getCSSVariable('--gray-150')
        }
      }
    },
    yAxis: {
      type: 'category',
      data: data.map((item) => item.tool_name),
      axisLine: {
        lineStyle: {
          color: getCSSVariable('--gray-200')
        }
      },
      axisLabel: {
        color: getCSSVariable('--gray-500'),
        interval: 0
      }
    },
    series: [
      {
        name: '调用次数',
        type: 'bar',
        data: data.map((item) => item.count),
        itemStyle: {
          color: getColorByIndex(0),
          borderRadius: [0, 4, 4, 0]
        },
        emphasis: {
          itemStyle: {
            color: getColorByIndex(0),
            shadowBlur: 10,
            shadowColor: getCSSVariable('--color-info-50')
          }
        }
      }
    ]
  }

  toolsChart.setOption(option)
}

// 更新图表
const updateCharts = () => {
  nextTick(() => {
    initToolsChart()
  })
}

// 监听数据变化
watch(
  [() => props.toolStats, () => props.loading],
  () => {
    updateCharts()
  },
  { deep: true }
)

// 窗口大小变化时重新调整图表
const handleResize = () => {
  if (toolsChart) toolsChart.resize()
}

onMounted(() => {
  updateCharts()
  window.addEventListener('resize', handleResize)
})

// 监听主题变化，重新渲染图表
watch(
  () => themeStore.isDark,
  () => {
    if (props.toolStats && toolsChart) {
      nextTick(() => {
        updateCharts()
      })
    }
  }
)

// 组件卸载时清理
const cleanup = () => {
  resizeObserver?.disconnect()
  window.removeEventListener('resize', handleResize)
  if (toolsChart) {
    toolsChart.dispose()
    toolsChart = null
  }
}

onBeforeUnmount(cleanup)

// 导出清理函数供父组件调用
defineExpose({
  cleanup
})
</script>

<style scoped lang="less">
.chart-container h4 {
  color: var(--color-text);
}
</style>
