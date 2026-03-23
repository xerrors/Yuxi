<template>
  <div class="dashboard-container">
    <!-- 顶部状态条 -->

    <!-- 现代化顶部统计栏 -->
    <div class="modern-stats-header">
      <StatusBar />
      <StatsOverviewComponent :basic-stats="basicStats" @open-feedback="handleOpenFeedback" />
    </div>

    <!-- Grid布局的主要内容区域 -->
    <div class="dashboard-grid">
      <!-- 调用统计模块 - 占据2x1网格 -->
      <CallStatsComponent :loading="loading" ref="callStatsRef" />

      <!-- 用户活跃度分析 - 占据1x1网格 -->
      <div class="grid-item user-stats">
        <UserStatsComponent
          :user-stats="allStatsData?.users"
          :loading="loading"
          ref="userStatsRef"
        />
      </div>

      <!-- AI智能体分析 - 占据1x1网格 -->
      <div class="grid-item agent-stats">
        <AgentStatsComponent
          :agent-stats="allStatsData?.agents"
          :loading="loading"
          ref="agentStatsRef"
        />
      </div>

      <!-- 工具调用监控 - 占据1x1网格 -->
      <div class="grid-item tool-stats">
        <ToolStatsComponent
          :tool-stats="allStatsData?.tools"
          :loading="loading"
          ref="toolStatsRef"
        />
      </div>

      <!-- 知识库使用情况 - 占据1x1网格 -->
      <div class="grid-item knowledge-stats">
        <KnowledgeStatsComponent
          :knowledge-stats="allStatsData?.knowledge"
          :loading="loading"
          ref="knowledgeStatsRef"
        />
      </div>
    </div>

    <!-- 反馈模态框 -->
    <FeedbackModalComponent ref="feedbackModal" />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'

// 导入子组件
import StatusBar from '@/components/StatusBar.vue'
import UserStatsComponent from '@/components/dashboard/UserStatsComponent.vue'
import ToolStatsComponent from '@/components/dashboard/ToolStatsComponent.vue'
import KnowledgeStatsComponent from '@/components/dashboard/KnowledgeStatsComponent.vue'
import AgentStatsComponent from '@/components/dashboard/AgentStatsComponent.vue'
import CallStatsComponent from '@/components/dashboard/CallStatsComponent.vue'
import StatsOverviewComponent from '@/components/dashboard/StatsOverviewComponent.vue'
import FeedbackModalComponent from '@/components/dashboard/FeedbackModalComponent.vue'

// 组件引用
const feedbackModal = ref(null)

// 统计数据 - 使用新的响应式结构
const basicStats = ref({})
const allStatsData = ref({
  users: null,
  tools: null,
  knowledge: null,
  agents: null
})

// 对话列表
const loading = ref(false)
const loadingDetail = ref(false)

// 调用统计子组件引用
const callStatsRef = ref(null)

// 子组件引用
const userStatsRef = ref(null)
const toolStatsRef = ref(null)
const knowledgeStatsRef = ref(null)
const agentStatsRef = ref(null)

// 加载统计数据 - 使用并行API调用
const loadAllStats = async () => {
  loading.value = true
  try {
    // 使用并行API调用获取所有统计数据
    const response = await dashboardApi.getAllStats()

    // 更新基础统计数据
    basicStats.value = response.basic

    // 更新详细统计数据
    allStatsData.value = {
      users: response.users,
      tools: response.tools,
      knowledge: response.knowledge,
      agents: response.agents
    }

    console.log('Dashboard 数据加载完成:', response)
    message.success('数据加载成功')
  } catch (error) {
    console.error('加载统计数据失败:', error)
    message.error('加载统计数据失败')

    // 如果并行请求失败，尝试单独加载基础数据
    try {
      const basicResponse = await dashboardApi.getStats()
      basicStats.value = basicResponse
      message.warning('详细数据加载失败，仅显示基础统计')
    } catch (basicError) {
      console.error('加载基础统计数据也失败:', basicError)
      message.error('无法加载任何统计数据')
    }
  } finally {
    loading.value = false
  }
}

// 保留原有的loadStats函数以兼容旧代码
const loadStats = loadAllStats

// 打开反馈详情弹窗
const handleOpenFeedback = () => {
  feedbackModal.value?.show()
}

// 清理函数 - 清理所有子组件的图表实例
const cleanupCharts = () => {
  if (userStatsRef.value?.cleanup) userStatsRef.value.cleanup()
  if (toolStatsRef.value?.cleanup) userStatsRef.value.cleanup()
  if (knowledgeStatsRef.value?.cleanup) knowledgeStatsRef.value.cleanup()
  if (agentStatsRef.value?.cleanup) agentStatsRef.value.cleanup()
  if (callStatsRef.value?.cleanup) callStatsRef.value.cleanup()
}

// 初始化
onMounted(() => {
  loadAllStats()
})

// 组件卸载时清理图表
onUnmounted(() => {
  cleanupCharts()
})
</script>

<style scoped lang="less">
.dashboard-container {
  // padding: 0 24px 24px 24px;
  background-color: var(--gray-25);
  min-height: calc(100vh - 64px);
  overflow-x: hidden;
}

// Dashboard 特有的网格布局
.dashboard-grid {
  display: grid;
  padding: 16px;
  grid-template-columns: 1fr 1fr 1fr;
  grid-template-rows: auto auto;
  gap: 16px;
  margin-bottom: 24px;
  min-height: 600px;

  .grid-item {
    border-radius: 8px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    min-height: 300px;
    background-color: transparent;
    border: none;
    transition: all 0.2s ease;

    &:hover {
      .conversations-section,
      .call-stats-section {
        border-color: var(--gray-200);
        box-shadow: 0 1px 3px 0 var(--shadow-100);
      }
    }

    // 大页面布局：第一行 2x1 + 1x1，第二行 3x1x1
    &.call-stats {
      grid-column: 1 / 3;
      grid-row: 1 / 2;
      min-height: 400px;
    }

    &.user-stats {
      grid-column: 3 / 4;
      grid-row: 1 / 2;
      min-height: 400px;
    }

    &.agent-stats {
      grid-column: 1 / 2;
      grid-row: 2 / 3;
      min-height: 350px;
    }

    &.tool-stats {
      grid-column: 2 / 3;
      grid-row: 2 / 3;
      min-height: 350px;
    }

    &.knowledge-stats {
      grid-column: 3 / 4;
      grid-row: 2 / 3;
      min-height: 350px;
    }
  }
}

// Dashboard 特有的卡片样式
.call-stats-section {
  background-color: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 12px;
  transition: all 0.2s ease;
  box-shadow: none;

  &:hover {
    background-color: var(--gray-25);
    border-color: var(--gray-200);
    box-shadow: 0 1px 3px 0 var(--shadow-100);
  }

  :deep(.ant-card-head) {
    border-bottom: 1px solid var(--gray-200);
    min-height: 56px;
    padding: 0 20px;
    background-color: var(--gray-0);

    .ant-card-head-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-1000);
    }
  }

  :deep(.ant-card-body) {
    padding: 16px 20px;
    background-color: var(--gray-0);
  }

  :deep(.ant-card-extra) {
    .ant-space {
      gap: 8px;
    }
  }
}

// Dashboard 特有的占位符样式
.placeholder-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--gray-600);

  .placeholder-icon {
    width: 64px;
    height: 64px;
    background-color: var(--gray-100);
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 16px;

    .icon {
      width: 32px;
      height: 32px;
      color: var(--gray-500);
    }
  }

  .placeholder-text {
    font-size: 18px;
    font-weight: 600;
    color: var(--gray-800);
    margin-bottom: 8px;
  }

  .placeholder-subtitle {
    font-size: 14px;
    color: var(--gray-600);
  }
}

// 调用统计模块样式
.call-stats-section {
  .call-stats-container {
    .call-summary {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 24px;

      .summary-card {
        background: linear-gradient(135deg, var(--gray-50) 0%, var(--gray-100) 100%);
        border: 1px solid var(--gray-200);
        border-radius: 8px;
        padding: 12px;
        text-align: center;

        .summary-value {
          font-size: 16px;
          font-weight: 600;
          color: var(--gray-800);
          margin-bottom: 4px;
        }

        .summary-label {
          font-size: 11px;
          color: var(--gray-500);
          font-weight: 500;
        }
      }
    }

    .chart-container {
      .chart {
        width: 100%;
        height: 280px;
        border-radius: 8px;
        overflow: hidden;
      }
    }
  }

  :deep(.ant-card-extra) {
    .ant-space {
      gap: 8px;
    }
  }
}

// Dashboard 特有的响应式设计
@media (max-width: 1200px) {
  .dashboard-grid {
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto auto auto;
    gap: 16px;

    .grid-item {
      // 小页面布局：第一行 2x1，第二行和第三行各是 2x1x1
      &.call-stats {
        grid-column: 1 / 3;
        grid-row: 1 / 2;
        min-height: 350px;
      }

      &.user-stats {
        grid-column: 1 / 2;
        grid-row: 2 / 3;
        min-height: 300px;
      }

      &.agent-stats {
        grid-column: 2 / 3;
        grid-row: 2 / 3;
        min-height: 300px;
      }

      &.tool-stats {
        grid-column: 1 / 2;
        grid-row: 3 / 4;
        min-height: 300px;
      }

      &.knowledge-stats {
        grid-column: 2 / 3;
        grid-row: 3 / 4;
        min-height: 300px;
      }
    }
  }
}

@media (max-width: 768px) {
  .dashboard-container {
    padding: 16px;
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
    gap: 12px;

    .grid-item {
      &.call-stats,
      &.agent-stats,
      &.user-stats,
      &.tool-stats,
      &.knowledge-stats {
        grid-column: 1 / 2;
        grid-row: auto;
        min-height: 300px;
      }
    }
  }

  .call-stats-section {
    .call-stats-container {
      .call-summary {
        grid-template-columns: repeat(2, 1fr);
        gap: 12px;

        .summary-card {
          padding: 12px;

          .summary-value {
            font-size: 18px;
          }

          .summary-label {
            font-size: 11px;
          }
        }
      }

      .chart-container {
        .chart {
          height: 200px;
        }
      }
    }
  }

  .placeholder-content {
    height: 150px;

    .placeholder-icon {
      width: 48px;
      height: 48px;
      margin-bottom: 12px;

      .icon {
        width: 24px;
        height: 24px;
      }
    }

    .placeholder-text {
      font-size: 16px;
    }

    .placeholder-subtitle {
      font-size: 12px;
    }
  }
}
</style>
