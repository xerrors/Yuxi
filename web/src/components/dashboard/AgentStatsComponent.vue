<template>
  <a-card title="AI智能体分析" :loading="loading" class="dashboard-card">
    <!-- 智能体概览 -->
    <div class="dashboard-card-metric-grid">
      <DashboardMetricCard
        :icon="Bot"
        :value="formatNumber(agentStats?.total_agents)"
        label="智能体总数"
        tone="info"
        compact
      />
      <DashboardMetricCard
        :icon="MessageSquare"
        :value="formatNumber(totalConversations)"
        label="总对话数"
        tone="accent"
        compact
      />
      <DashboardMetricCard
        :icon="Wrench"
        :value="formatNumber(totalToolUsage)"
        label="工具调用总数"
        tone="warning"
        compact
      />
    </div>

    <a-divider />

    <!-- 图表区域 -->
    <a-row :gutter="24">
      <!-- 对话数和工具调用数分布 -->
      <a-col :span="24">
        <div class="chart-container">
          <h4>对话/工具调用分布 (TOP 3)</h4>
          <div ref="conversationToolChartRef" class="chart"></div>
        </div>
      </a-col>
    </a-row>

  </a-card>
</template>

<script setup>
import { ref, onMounted, watch, nextTick, computed } from 'vue'
import * as echarts from '@/utils/dashboardCharts'
import { getColorByIndex } from '@/utils/chartColors'
import { useThemeStore } from '@/stores/theme'
import { formatNumber } from '@/utils/dashboard'
import { Bot, MessageSquare, Wrench } from '@lucide/vue'
import DashboardMetricCard from './DashboardMetricCard.vue'

// CSS 变量解析工具函数
function getCSSVariable(variableName, element = document.documentElement) {
  return getComputedStyle(element).getPropertyValue(variableName).trim()
}

// theme store
const themeStore = useThemeStore()

// Props
const props = defineProps({
  agentStats: {
    type: Object,
    default: () => ({})
  },
  loading: {
    type: Boolean,
    default: false
  }
})

// Chart refs
const conversationToolChartRef = ref(null)
let conversationToolChart = null

// 计算属性
const totalConversations = computed(() => {
  const conversationCounts = props.agentStats?.agent_conversation_counts || []
  return conversationCounts.reduce((sum, item) => sum + item.conversation_count, 0)
})

const totalToolUsage = computed(() => {
  const toolUsage = props.agentStats?.agent_tool_usage || []
  return toolUsage.reduce((sum, item) => sum + item.tool_usage_count, 0)
})

const agentNames = computed(() => props.agentStats?.agent_names || {})

const resolveAgentName = (agentId) => agentNames.value[agentId] || agentId

// 初始化对话数和工具调用数合并图表
const initConversationToolChart = () => {
  if (
    !conversationToolChartRef.value ||
    (!props.agentStats?.agent_conversation_counts?.length &&
      !props.agentStats?.agent_tool_usage?.length)
  )
    return

  // 如果已存在图表实例，先销毁
  if (conversationToolChart) {
    conversationToolChart.dispose()
    conversationToolChart = null
  }

  conversationToolChart = echarts.init(conversationToolChartRef.value)

  const conversationData = props.agentStats.agent_conversation_counts || []
  const toolData = props.agentStats.agent_tool_usage || []

  // 获取所有智能体ID并按对话数+工具调用数排序，取前3个
  const allAgentStats = {}

  // 统计每个智能体的总数据量（对话数 + 工具调用数）
  conversationData.forEach((item) => {
    if (!allAgentStats[item.agent_id]) {
      allAgentStats[item.agent_id] = { conversation: 0, tool: 0, total: 0 }
    }
    allAgentStats[item.agent_id].conversation = item.conversation_count
    allAgentStats[item.agent_id].total += item.conversation_count
  })

  toolData.forEach((item) => {
    if (!allAgentStats[item.agent_id]) {
      allAgentStats[item.agent_id] = { conversation: 0, tool: 0, total: 0 }
    }
    allAgentStats[item.agent_id].tool = item.tool_usage_count
    allAgentStats[item.agent_id].total += item.tool_usage_count
  })

  // 按总数据量降序排序，取前3个
  const topAgentIds = Object.entries(allAgentStats)
    .sort(([, a], [, b]) => b.total - a.total)
    .slice(0, 3)
    .map(([agentId]) => agentId)

  const option = {
    tooltip: {
      trigger: 'axis',
      backgroundColor: getCSSVariable('--gray-0'),
      borderColor: getCSSVariable('--gray-200'),
      borderWidth: 1,
      textStyle: {
        color: getCSSVariable('--gray-600')
      }
    },
    legend: {
      data: ['对话数', '工具调用数'],
      right: '0%',
      top: '0%',
      orient: 'horizontal',
      textStyle: {
        color: getCSSVariable('--gray-500')
      }
    },
    grid: {
      left: '3%',
      right: '15%',
      bottom: '3%',
      top: '10%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: topAgentIds.map(resolveAgentName),
      axisLine: {
        lineStyle: {
          color: getCSSVariable('--gray-200')
        }
      },
      axisLabel: {
        color: getCSSVariable('--gray-500'),
        interval: 0
        // rotate: 45
      }
    },
    yAxis: {
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
    series: [
      {
        name: '对话数',
        type: 'bar',
        data: topAgentIds.map((agentId) => {
          const item = conversationData.find((d) => d.agent_id === agentId)
          return item ? item.conversation_count : 0
        }),
        itemStyle: {
          color: getColorByIndex(0),
          borderRadius: [4, 4, 0, 0]
        },
        emphasis: {
          itemStyle: {
            color: getColorByIndex(0),
            shadowBlur: 10,
            shadowColor: getCSSVariable('--color-info-50')
          }
        }
      },
      {
        name: '工具调用数',
        type: 'bar',
        data: topAgentIds.map((agentId) => {
          const item = toolData.find((d) => d.agent_id === agentId)
          return item ? item.tool_usage_count : 0
        }),
        itemStyle: {
          color: getColorByIndex(1),
          borderRadius: [4, 4, 0, 0]
        },
        emphasis: {
          itemStyle: {
            color: getColorByIndex(1),
            shadowBlur: 10,
            shadowColor: getCSSVariable('--color-info-50')
          }
        }
      }
    ]
  }

  conversationToolChart.setOption(option)
}

// 更新图表
const updateCharts = () => {
  nextTick(() => {
    initConversationToolChart()
  })
}

// 监听数据变化
watch(
  () => props.agentStats,
  () => {
    updateCharts()
  },
  { deep: true }
)

// 窗口大小变化时重新调整图表
const handleResize = () => {
  if (conversationToolChart) conversationToolChart.resize()
}

onMounted(() => {
  updateCharts()
  window.addEventListener('resize', handleResize)
})

// 监听主题变化，重新渲染图表
watch(
  () => themeStore.isDark,
  () => {
    if (props.agentStats && conversationToolChart) {
      nextTick(() => {
        updateCharts()
      })
    }
  }
)

// 组件卸载时清理
const cleanup = () => {
  window.removeEventListener('resize', handleResize)
  if (conversationToolChart) {
    conversationToolChart.dispose()
    conversationToolChart = null
  }
}

// 导出清理函数供父组件调用
defineExpose({
  cleanup
})
</script>
