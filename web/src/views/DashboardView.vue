<template>
  <div class="dashboard-container">
    <PageHeader
      v-model:active-key="activeTab"
      title="数据总览"
      :tabs="dashboardTabs"
      :loading="loading"
      :show-border="true"
      aria-label="数据总览视图切换"
      @change="handleTabChange"
    >
      <template #info>
        <span class="header-context">
          {{ activeTab === 'overview' ? '系统运行与资源使用' : '会话趋势与历史审计' }}
        </span>
      </template>
      <template #actions>
        <div class="dashboard-header-actions">
          <a-tooltip title="系统设置">
            <button
              type="button"
              class="header-action-button"
              aria-label="系统设置"
              @click="openSettings"
            >
              <Settings class="header-action-icon" />
            </button>
          </a-tooltip>
          <a-tooltip :title="themeStore.isDark ? '切换到浅色模式' : '切换到深色模式'">
            <button
              type="button"
              class="header-action-button"
              aria-label="切换主题"
              @click="toggleTheme"
            >
              <Sun v-if="themeStore.isDark" class="header-action-icon" />
              <Moon v-else class="header-action-icon" />
            </button>
          </a-tooltip>
          <a-tooltip title="任务中心">
            <button
              type="button"
              class="header-action-button task-center-button"
              :class="{ active: taskerStore.isDrawerOpen }"
              aria-label="任务中心"
              @click="openTaskCenter"
            >
              <ClipboardList class="header-action-icon" />
              <span class="task-center-label">任务中心</span>
              <a-badge :count="activeTaskCount" :overflow-count="99" size="small" />
            </button>
          </a-tooltip>
        </div>
      </template>
    </PageHeader>

    <StatsOverviewComponent
      v-if="overviewActivated"
      v-show="activeTab === 'overview'"
      :basic-stats="basicStats"
      @open-feedback="handleOpenFeedback"
    />

    <!-- Tab 1: 系统概览主要内容区域 -->
    <div
      v-if="overviewActivated"
      v-show="activeTab === 'overview'"
      class="dashboard-grid"
      :class="{ 'without-knowledge': !knowledgeEnabled }"
    >
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
      <div v-if="knowledgeEnabled" class="grid-item knowledge-stats">
        <KnowledgeStatsComponent
          :knowledge-stats="allStatsData?.knowledge"
          :loading="loading"
          ref="knowledgeStatsRef"
        />
      </div>
    </div>

    <!-- Tab 2: 会话（Thread）多维分析试点 -->
    <div v-if="threadActivated" v-show="activeTab === 'threads'" class="thread-tab-container">
      <ThreadStatsComponent ref="threadStatsRef" />
    </div>

    <!-- 反馈模态框 -->
    <FeedbackModalComponent ref="feedbackModal" />
  </div>
</template>

<script setup>
import { computed, inject, ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { message } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import { dashboardApi } from '@/apis/dashboard_api'
import { useRuntimeCapabilitiesStore } from '@/stores/runtimeCapabilities'
import { useTaskerStore } from '@/stores/tasker'
import { useThemeStore } from '@/stores/theme'
import { useUserStore } from '@/stores/user'
import { ClipboardList, Settings, Sun, Moon } from '@lucide/vue'

// 导入子组件
import PageHeader from '@/components/shared/PageHeader.vue'
import UserStatsComponent from '@/components/dashboard/UserStatsComponent.vue'
import ToolStatsComponent from '@/components/dashboard/ToolStatsComponent.vue'
import KnowledgeStatsComponent from '@/components/dashboard/KnowledgeStatsComponent.vue'
import AgentStatsComponent from '@/components/dashboard/AgentStatsComponent.vue'
import CallStatsComponent from '@/components/dashboard/CallStatsComponent.vue'
import StatsOverviewComponent from '@/components/dashboard/StatsOverviewComponent.vue'
import FeedbackModalComponent from '@/components/dashboard/FeedbackModalComponent.vue'
import ThreadStatsComponent from '@/components/dashboard/ThreadStatsComponent.vue'

const route = useRoute()
const router = useRouter()
const dashboardTabs = [
  { key: 'overview', label: '系统概览' },
  { key: 'threads', label: '会话分析' }
]
const normalizeTab = (tab) => (tab === 'threads' ? 'threads' : 'overview')
const activeTab = ref(normalizeTab(route.query.tab))
const overviewActivated = ref(activeTab.value === 'overview')
const threadActivated = ref(activeTab.value === 'threads')

// 组件引用
const feedbackModal = ref(null)
const runtimeCapabilitiesStore = useRuntimeCapabilitiesStore()
const taskerStore = useTaskerStore()
const themeStore = useThemeStore()
const userStore = useUserStore()
const { knowledgeEnabled } = storeToRefs(runtimeCapabilitiesStore)
const { activeCount } = storeToRefs(taskerStore)
const { openSettingsModal } = inject('settingsModal', {})
const activeTaskCount = computed(() => activeCount.value || 0)

// 统计数据
const basicStats = ref({})
const allStatsData = ref({
  users: null,
  tools: null,
  knowledge: null,
  agents: null
})

const loading = ref(false)
const overviewLoaded = ref(false)

// 子组件引用
const callStatsRef = ref(null)
const userStatsRef = ref(null)
const toolStatsRef = ref(null)
const knowledgeStatsRef = ref(null)
const agentStatsRef = ref(null)
const threadStatsRef = ref(null)

// 加载概览统计数据
const loadAllStats = async () => {
  if (overviewLoaded.value) return
  loading.value = true
  try {
    const response = await dashboardApi.getAllStats({
      includeKnowledge: knowledgeEnabled.value
    })

    basicStats.value = response.basic
    allStatsData.value = {
      users: response.users,
      tools: response.tools,
      knowledge: response.knowledge,
      agents: response.agents
    }
    overviewLoaded.value = true
  } catch (error) {
    console.error('加载统计数据失败:', error)
    try {
      const basicResponse = await dashboardApi.getStats()
      basicStats.value = basicResponse
      overviewLoaded.value = true
      message.warning('详细统计暂不可用，当前仅显示基础指标')
    } catch (basicError) {
      console.error('加载基础统计数据失败:', basicError)
      message.error('无法加载统计数据')
    }
  } finally {
    loading.value = false
  }
}

// 切换 Tab 处理
const handleTabChange = async ({ key }) => {
  if (key === 'overview') {
    overviewActivated.value = true
    await loadAllStats()
    await nextTick()
    if (userStatsRef.value?.updateCharts) userStatsRef.value.updateCharts()
    if (toolStatsRef.value?.updateCharts) toolStatsRef.value.updateCharts()
    if (knowledgeStatsRef.value?.updateCharts) knowledgeStatsRef.value.updateCharts()
    if (agentStatsRef.value?.updateCharts) agentStatsRef.value.updateCharts()
    return
  }

  threadActivated.value = true
  await nextTick()
  threadStatsRef.value?.resizeCharts?.()
}

watch(activeTab, (tab) => {
  const normalizedTab = normalizeTab(tab)
  if (normalizedTab === 'overview') overviewActivated.value = true
  if (normalizedTab === 'threads') threadActivated.value = true
  const query = { ...route.query }
  if (normalizedTab === 'overview') delete query.tab
  else query.tab = normalizedTab
  if (route.query.tab !== query.tab) router.replace({ query })
})

watch(
  () => route.query.tab,
  (tab) => {
    const normalizedTab = normalizeTab(tab)
    if (activeTab.value !== normalizedTab) activeTab.value = normalizedTab
  }
)

const openSettings = () => {
  openSettingsModal?.(userStore.isAdmin ? 'base' : 'account')
}

const toggleTheme = () => {
  themeStore.toggleTheme()
}

const openTaskCenter = () => {
  taskerStore.openDrawer()
}

// 打开反馈详情弹窗
const handleOpenFeedback = () => {
  feedbackModal.value?.show()
}

// 清理所有子组件的图表实例
const cleanupCharts = () => {
  if (userStatsRef.value?.cleanup) userStatsRef.value.cleanup()
  if (toolStatsRef.value?.cleanup) toolStatsRef.value.cleanup()
  if (knowledgeStatsRef.value?.cleanup) knowledgeStatsRef.value.cleanup()
  if (agentStatsRef.value?.cleanup) agentStatsRef.value.cleanup()
  if (callStatsRef.value?.cleanup) callStatsRef.value.cleanup()
  if (threadStatsRef.value?.cleanup) threadStatsRef.value.cleanup()
}

onMounted(async () => {
  await runtimeCapabilitiesStore.ensureLoaded()
  if (overviewActivated.value) await loadAllStats()
})

onUnmounted(() => {
  cleanupCharts()
})
</script>

<style scoped lang="less">
.dashboard-container {
  background-color: var(--gray-25);
  min-height: calc(100vh - 64px);
  overflow-x: hidden;
}

.header-context {
  color: var(--gray-500);
  font-size: 12px;
  white-space: nowrap;
}

.dashboard-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-action-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  min-width: 32px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--gray-600);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease;

  &:hover,
  &.active {
    border-color: var(--gray-150);
    background: var(--gray-0);
    color: var(--gray-900);
  }
}

.header-action-icon {
  width: 16px;
  height: 16px;
}

.task-center-button {
  padding-right: 10px;
}

.task-center-label {
  line-height: 1;
}

.thread-tab-container {
  width: 100%;
}

// Dashboard 特有的网格布局
.dashboard-grid {
  display: grid;
  padding: var(--page-padding);
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

  &.without-knowledge .grid-item.tool-stats {
    grid-column: 2 / 4;
  }
}

// 响应式设计
@media (max-width: 1200px) {
  .dashboard-grid {
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto auto auto;
    gap: 16px;

    .grid-item {
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

    &.without-knowledge .grid-item.tool-stats {
      grid-column: 1 / 3;
    }
  }
}

@media (max-width: 900px) {
  .header-context,
  .task-center-label {
    display: none;
  }

  .task-center-button {
    padding-right: 8px;
  }
}

@media (max-width: 768px) {
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

    &.without-knowledge .grid-item.tool-stats {
      grid-column: 1 / 2;
    }
  }
}
</style>
