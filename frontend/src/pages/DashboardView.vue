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
          <a-tooltip :title="`主题：${themeStore.themeLabel}（点击切换）`">
            <button
              type="button"
              class="header-action-button"
              :aria-label="`切换主题，当前${themeStore.themeLabel}`"
              @click="toggleTheme"
            >
              <Sun v-if="themeStore.themeMode === 'light'" class="header-action-icon" />
              <Moon v-else-if="themeStore.themeMode === 'dark'" class="header-action-icon" />
              <Monitor v-else class="header-action-icon" />
            </button>
          </a-tooltip>
          <a-tooltip title="后台作业">
            <button
              type="button"
              class="header-action-button job-center-button"
              :class="{ active: jobsStore.isDrawerOpen }"
              aria-label="后台作业"
              @click="openJobCenter"
            >
              <ClipboardList class="header-action-icon" />
              <a-badge :count="activeJobCount" :overflow-count="99" size="small" />
            </button>
          </a-tooltip>
        </div>
      </template>
    </PageHeader>

    <StatsOverviewComponent
      v-if="overviewActivated"
      v-show="activeTab === 'overview'"
      :basic-stats="basicStats"
      :basic-stats-loading="loading"
      :basic-stats-error="basicStatsError"
      :vm-stats="vmStats"
      :vm-stats-loading="vmStatsLoading"
      :vm-stats-error="vmStatsError"
      @refresh-vms="loadVmStats"
      @refresh-basic="retryBasicStats"
    />

    <div v-if="overviewActivated" v-show="activeTab === 'overview'" class="dashboard-grid">
      <CallStatsComponent ref="callStatsRef" />
      <div class="grid-item user-stats">
        <UserStatsComponent
          :user-stats="allStatsData?.users"
          :loading="loading"
          ref="userStatsRef"
        />
      </div>
      <ConversationDistributionCard />
      <div class="grid-item tool-stats">
        <ToolStatsComponent
          :tool-stats="allStatsData?.tools"
          :loading="loading"
          ref="toolStatsRef"
        />
      </div>
      <div class="grid-item knowledge-stats">
        <KnowledgeStatsComponent
          :knowledge-stats="allStatsData?.knowledge"
          :loading="loading"
          ref="knowledgeStatsRef"
        />
      </div>
    </div>
    <AgentFeedbackList v-if="activeTab === 'feedback' && userStore.isSuperAdmin" />
  </div>
</template>

<script setup>
import { computed, inject, ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { message } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import { dashboardApi } from '@/apis/dashboard_api'
import { useBackgroundJobsStore } from '@/modules/background-jobs/model/jobs'
import { useThemeStore } from '@/shared/model/theme'
import { useUserStore } from '@/modules/identity/model/user'
import { ClipboardList, Settings, Sun, Moon, Monitor } from '@lucide/vue'

// 导入子组件
import PageHeader from '@/shared/ui/PageHeader.vue'
import UserStatsComponent from '@/modules/dashboard/ui/UserStatsComponent.vue'
import ToolStatsComponent from '@/modules/dashboard/ui/ToolStatsComponent.vue'
import KnowledgeStatsComponent from '@/modules/dashboard/ui/KnowledgeStatsComponent.vue'
import CallStatsComponent from '@/modules/dashboard/ui/CallStatsComponent.vue'
import StatsOverviewComponent from '@/modules/dashboard/ui/StatsOverviewComponent.vue'
import ConversationDistributionCard from '@/modules/dashboard/ui/ConversationDistributionCard.vue'
import AgentFeedbackList from '@/modules/dashboard/ui/AgentFeedbackList.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const dashboardTabs = computed(() => [
  { key: 'overview', label: '系统概览' },
  ...(userStore.isSuperAdmin ? [{ key: 'feedback', label: '智能体反馈' }] : [])
])
const normalizeTab = (tab) => (tab === 'feedback' && userStore.isSuperAdmin ? tab : 'overview')
const activeTab = ref(normalizeTab(route.query.tab))
const overviewActivated = ref(activeTab.value === 'overview')

// 组件引用
const jobsStore = useBackgroundJobsStore()
const themeStore = useThemeStore()
const { activeCount } = storeToRefs(jobsStore)
const { openSettingsModal } = inject('settingsModal', {})
const activeJobCount = computed(() => activeCount.value || 0)

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

const loading = ref(false)
const overviewLoaded = ref(false)

// 子组件引用
const callStatsRef = ref(null)
const userStatsRef = ref(null)
const toolStatsRef = ref(null)
const knowledgeStatsRef = ref(null)

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

    basicStats.value = response.basic
    allStatsData.value = {
      users: response.users,
      tools: response.tools,
      knowledge: response.knowledge
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
      // 基础指标同样失败：卡片保持错误态，不把失败投影为 0。
      basicStats.value = {}
      basicStatsError.value = true
      message.error('无法加载统计数据')
    }
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

// 切换 Tab 处理
const handleTabChange = async ({ key }) => {
  if (key !== 'overview') return
  overviewActivated.value = true
  loadVmStats()
  await loadAllStats()
  await nextTick()
  callStatsRef.value?.resizeCharts()
  if (userStatsRef.value?.updateCharts) userStatsRef.value.updateCharts()
  if (toolStatsRef.value?.updateCharts) toolStatsRef.value.updateCharts()
  if (knowledgeStatsRef.value?.updateCharts) knowledgeStatsRef.value.updateCharts()
}

watch(
  activeTab,
  (tab) => {
    const normalizedTab = normalizeTab(tab)
    if (normalizedTab === 'overview') overviewActivated.value = true
    const query = { ...route.query }
    if (normalizedTab === 'overview') delete query.tab
    else query.tab = normalizedTab
    if (route.query.tab !== query.tab) router.replace({ query })
  },
  { immediate: true }
)

watch(
  () => route.query.tab,
  (tab) => {
    const normalizedTab = normalizeTab(tab)
    if (activeTab.value !== normalizedTab) {
      activeTab.value = normalizedTab
      handleTabChange({ key: normalizedTab })
    }
  }
)

const openSettings = () => {
  openSettingsModal?.(userStore.isAdmin ? 'base' : 'account')
}

const toggleTheme = () => {
  themeStore.toggleTheme()
}

const openJobCenter = () => {
  jobsStore.openDrawer()
}

// 清理所有子组件的图表实例
const cleanupCharts = () => {
  if (userStatsRef.value?.cleanup) userStatsRef.value.cleanup()
  if (toolStatsRef.value?.cleanup) toolStatsRef.value.cleanup()
  if (knowledgeStatsRef.value?.cleanup) knowledgeStatsRef.value.cleanup()
  if (callStatsRef.value?.cleanup) callStatsRef.value.cleanup()
}

onMounted(async () => {
  if (overviewActivated.value) await Promise.all([loadAllStats(), loadVmStats()])
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

:global(:root.dark .dashboard-container) {
  background-color: var(--bg-page);
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

.job-center-button {
  padding-right: 10px;
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
  .job-center-button {
    padding-right: 8px;
  }
  .dashboard-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
}
</style>
