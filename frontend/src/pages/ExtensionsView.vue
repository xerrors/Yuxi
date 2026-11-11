<template>
  <div class="extensions-view extension-page-root">
    <PageHeader
      v-if="!isDetailPage"
      v-model:active-key="activeTab"
      title="智能体扩展"
      :tabs="extensionTabs"
      :loading="activeChildLoading"
      :show-border="true"
      aria-label="智能体扩展视图切换"
    />

    <div v-if="!isDetailPage" class="extensions-content">
      <div v-if="userStore.isAdmin && activeTab === 'knowledge'" class="tab-panel">
        <KnowledgeBaseView ref="knowledgeRef" embedded />
      </div>
      <div v-if="userStore.isAdmin && activeTab === 'tools'" class="tab-panel">
        <ToolsCardList ref="toolsRef" />
      </div>
      <div v-if="activeTab === 'skills'" class="tab-panel">
        <SkillCardList ref="skillsRef" />
      </div>
      <div v-if="activeTab === 'mcp'" class="tab-panel">
        <McpCardList ref="mcpRef" />
      </div>
    </div>

    <router-view v-else :key="route.path" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import ToolsCardList from '@/modules/extensions/ui/ToolsCardList.vue'
import McpCardList from '@/modules/extensions/ui/McpCardList.vue'
import SkillCardList from '@/modules/extensions/ui/SkillCardList.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import KnowledgeBaseView from '@/modules/knowledge/ui/KnowledgeCatalog.vue'
import { useUserStore } from '@/modules/identity/model/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const activeTab = ref(null)
const knowledgeRef = ref(null)
const skillsRef = ref(null)
const mcpRef = ref(null)
const toolsRef = ref(null)

const adminExtensionTabs = computed(() => [
  { key: 'knowledge', label: '知识库' },
  { key: 'skills', label: '技能' },
  { key: 'tools', label: '工具' },
  { key: 'mcp', label: '连接器' }
])
const userExtensionTabs = [{ key: 'skills', label: '技能' }, { key: 'mcp', label: '连接器' }]
const extensionTabs = computed(() =>
  userStore.isAdmin ? adminExtensionTabs.value : userExtensionTabs
)
const allowedTabKeys = computed(() => extensionTabs.value.map((tab) => tab.key))
const defaultTabKey = computed(() => extensionTabs.value[0]?.key || 'skills')

const normalizeTab = (tab) => {
  if (allowedTabKeys.value.includes(tab)) return tab
  return defaultTabKey.value
}

const replaceTabQuery = (tab) => {
  const query = { ...route.query }
  if (tab === defaultTabKey.value) {
    delete query.tab
  } else {
    query.tab = tab
  }
  router.replace({ query })
}

const isDetailPage = computed(() => {
  return (
    route.path.startsWith('/extensions/knowledge-bases/') ||
    route.path.startsWith('/extensions/mcp/') ||
    route.path.startsWith('/extensions/skill/')
  )
})

const activeChildLoading = computed(() => {
  const refMap = {
    knowledge: knowledgeRef,
    tools: toolsRef,
    skills: skillsRef,
    mcp: mcpRef
  }
  const child = refMap[activeTab.value]
  return child?.value?.loading || false
})

watch(
  () => [route.query.tab, userStore.isAdmin],
  ([tab]) => {
    const nextTab = normalizeTab(tab)
    if (activeTab.value !== nextTab) activeTab.value = nextTab
    if (tab && tab !== nextTab) {
      message.info('该扩展标签不可用，已切换到可访问的标签')
      replaceTabQuery(nextTab)
    }
  },
  { immediate: true }
)

watch(activeTab, (tab) => {
  if (!tab) return
  const nextTab = normalizeTab(tab)
  if (nextTab !== tab) {
    activeTab.value = nextTab
    return
  }
  if (route.query.tab === nextTab || (!route.query.tab && nextTab === defaultTabKey.value)) return
  replaceTabQuery(nextTab)
})
</script>

<style scoped lang="less">

.extensions-view {
  .extensions-content {
    flex: 1;
    min-height: 0;
    overflow: hidden;

    .tab-panel {
      height: 100%;
      min-height: 0;
      overflow-y: auto;
    }
  }
}
</style>
