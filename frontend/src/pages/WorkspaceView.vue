<template>
  <div class="workspace-view layout-container">
    <PageHeader
      v-model:active-key="activeTab"
      title="网盘"
      :tabs="spaceTabs"
      :loading="activeTab === 'workspace' && Boolean(workspaceBrowserRef?.loading)"
      :show-border="true"
      aria-label="网盘视图切换"
    >
      <template v-if="activeTab === 'workspace'" #actions>
        <a-button
          class="lucide-icon-btn"
          :disabled="!workspaceBrowserRef"
          :aria-label="workspaceBrowserRef?.sidebarCollapsed ? '展开工作区侧边栏' : '收起工作区侧边栏'"
          @click="workspaceBrowserRef.sidebarCollapsed = !workspaceBrowserRef.sidebarCollapsed"
        >
          {{ workspaceBrowserRef?.sidebarCollapsed ? '展开导航' : '收起导航' }}
        </a-button>
        <a-button
          class="lucide-icon-btn"
          :disabled="!workspaceBrowserRef"
          @click="workspaceBrowserRef.fileSearchOpen = true"
        >
          <template #icon><Search :size="16" /></template>
          搜索
        </a-button>
        <a-button
          v-if="workspaceBrowserRef?.isAgentsWorkspacePath"
          class="lucide-icon-btn"
          @click="workspaceBrowserRef.openAgentsGuideModal()"
        >
          <template #icon><CircleHelp :size="16" /></template>
          使用说明
        </a-button>
      </template>
    </PageHeader>

    <WorkspaceBrowser
      v-if="workspaceVisited"
      v-show="activeTab === 'workspace'"
      ref="workspaceBrowserRef"
      :active="activeTab === 'workspace'"
    />
    <KeepAlive>
      <DeliverablesBrowser v-if="activeTab === 'deliverables'" />
    </KeepAlive>
    <WPSCloudDrive v-if="activeTab === 'cloud'" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CircleHelp, Search } from '@lucide/vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import WorkspaceBrowser from '@/modules/workspace/ui/WorkspaceBrowser.vue'
import DeliverablesBrowser from '@/modules/workspace/ui/DeliverablesBrowser.vue'
import WPSCloudDrive from '@/modules/workspace/ui/WPSCloudDrive.vue'

const route = useRoute()
const router = useRouter()
const workspaceBrowserRef = ref(null)
const spaceTabs = [
  { key: 'deliverables', label: '我的成果' },
  { key: 'workspace', label: '工作区' },
  { key: 'cloud', label: '我的云盘' }
]
const activeTab = computed({
  // 旧链接和文件搜索入口继续打开工作区。
  get: () => (['deliverables', 'cloud'].includes(route.query.tab) ? route.query.tab : 'workspace'),
  // 切换页签时移除一次性文件打开参数，保留其他查询条件。
  set: (tab) => {
    const query = { ...route.query }
    delete query.open
    if (tab === 'workspace') delete query.tab
    else query.tab = tab
    router.replace({ query })
  }
})

const workspaceVisited = ref(false)
watch(
  activeTab,
  (tab) => {
    if (tab === 'workspace') workspaceVisited.value = true
  },
  { immediate: true }
)
</script>

<style scoped lang="less">
.workspace-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  min-height: 0;

  :deep(.page-header) {
    flex-shrink: 0;
  }
}

@media (max-width: 640px) {
  .workspace-view :deep(.page-header) {
    height: auto;
    min-height: 48px;
    flex-wrap: wrap;
    padding: 8px 12px;
    gap: 8px;
  }

  .workspace-view :deep(.tab-item) {
    padding: 0 8px;
  }
}
</style>
