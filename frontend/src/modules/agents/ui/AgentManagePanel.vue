<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, RefreshCw, Trash2, SquarePen, Bot, ChevronRight } from '@lucide/vue'
import { useRouter } from 'vue-router'

import { agentApi } from '@/apis/agent_api'
import AgentEditModal from '@/modules/agents/ui/AgentEditModal.vue'
import { isBuiltinAgent, useAgentStore } from '@/modules/agents/model/agent'
import PageShoulder from '@/shared/ui/PageShoulder.vue'
import InfoCard from '@/shared/ui/InfoCard.vue'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import ExtensionCardGrid from '@/modules/extensions/ui/ExtensionCardGrid.vue'
import { normalizeAgentBackendOption } from '@/modules/agents/model/agentConfigUtils'
import { filterAndSortAgents } from '@/modules/agents/model/agentOrder'
import { getShareConfigLabel } from '@/modules/agents/model/shareConfig'

const props = defineProps({ focusAgent: { type: String, default: '' } })
const agentStore = useAgentStore()
const router = useRouter()
const agentLoading = ref(false)
const searchQuery = ref('')
const visibilityFilter = ref('all')

const agentBackendOptions = ref([])
const managedAgents = ref([])
const agentEditModalRef = ref(null)

const filteredAgents = computed(() => filterAndSortAgents(managedAgents.value, searchQuery.value))

const visibleAgents = computed(() =>
  visibilityFilter.value === 'all'
    ? filteredAgents.value
    : filteredAgents.value.filter((agent) => agent.visibility === visibilityFilter.value)
)

const visibilityFilterOptions = computed(() => [
  { value: 'all', label: '全部', count: managedAgents.value.length },
  {
    value: 'shared',
    label: '共享',
    count: managedAgents.value.filter((agent) => agent.visibility === 'shared').length
  },
  {
    value: 'private',
    label: '私有',
    count: managedAgents.value.filter((agent) => agent.visibility === 'private').length
  }
])

const agentStats = computed(() => ({
  total: managedAgents.value.length,
  builtin: managedAgents.value.filter(isBuiltinAgent).length,
  manageable: managedAgents.value.filter((agent) => agent.can_manage).length,
  global: managedAgents.value.filter(
    (agent) => (agent.share_config?.read_scope || agent.share_config)?.access_level === 'global'
  ).length
}))
const canManageAgent = (agent) => !!agent?.can_manage
/** 返回智能体共享范围的简短展示文案。 */
const getAgentShareLabel = (agent) =>
  agent?.visibility === 'private'
    ? '私有'
    : getShareConfigLabel(agent?.share_config, agent?.share_config_invalid)

// ============ Agent Operations ============
const loadAgentBackends = async () => {
  try {
    const response = await agentApi.getAgentBackends()
    agentBackendOptions.value = (response.backends || [])
      .filter((backend) => backend.can_create)
      .map(normalizeAgentBackendOption)
  } catch (error) {
    message.error(error.message || '加载智能体后端失败')
  }
}

const loadAgents = async () => {
  agentLoading.value = true
  try {
    const response = await agentApi.getAgents()
    managedAgents.value = response.agents || []
  } finally {
    agentLoading.value = false
  }
}

const refreshAgents = async () => {
  try {
    await loadAgents()
  } catch (error) {
    message.error(error.message || '加载智能体失败')
  }
}

const handleAgentSaved = async () => {
  try {
    await refreshAgentLists()
  } catch (error) {
    message.error(error.message || '刷新智能体列表失败')
  }
}

const openCreateAgentModal = () => {
  agentEditModalRef.value?.openCreate()
}

const openEditAgentModal = (agent) => {
  if (!canManageAgent(agent)) return
  agentEditModalRef.value?.openEdit(agent)
}

const openAgentChat = (agent) => {
  if (!agent?.agent_id || !agent.can_run) return
  router.push({ name: 'AgentComp', query: { agent_id: agent.agent_id } })
}

const refreshAgentLists = async () => {
  await Promise.all([loadAgents(), agentStore.fetchAgents()])
}

const deleteAgent = async (agent) => {
  if (isBuiltinAgent(agent)) {
    message.warning('内置智能体不能删除')
    return
  }
  Modal.confirm({
    title: `删除 ${agent.name}`,
    content: '删除后不可恢复，已绑定该智能体的历史对话仍保留原始绑定信息。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await agentApi.deleteAgent(agent.agent_id)
        await refreshAgentLists()
        message.success('智能体已删除')
      } catch (error) {
        message.error(error.message || '删除智能体失败')
      }
    }
  })
}

watch(
  () => props.focusAgent,
  (slug) => {
    if (slug && managedAgents.value.length) agentEditModalRef.value?.openEdit(slug)
  }
)

onMounted(async () => {
  const results = await Promise.allSettled([loadAgentBackends(), loadAgents()])
  if (props.focusAgent) await agentEditModalRef.value?.openEdit(props.focusAgent)
  const failedLoad = results.find((result) => result.status === 'rejected')
  if (failedLoad) {
    message.error(failedLoad.reason?.message || '加载智能体失败')
  }
})

defineExpose({
  loading: agentLoading,
  stats: agentStats,
  refresh: refreshAgents
})
</script>

<template>
  <div class="agent-manage-panel">
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索智能体...">
      <template #actions>
        <a-button
          class="yuxi-refresh-button lucide-icon-btn"
          @click="refreshAgents"
          :disabled="agentLoading"
          aria-label="刷新智能体"
        >
          <RefreshCw
            :size="14"
            class="page-shoulder-refresh-icon"
            :class="{ 'is-spinning': agentLoading }"
          />
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreateAgentModal">
          <Plus :size="14" />
          新增智能体
        </a-button>
      </template>
    </PageShoulder>

    <div class="agent-filter-row" role="group" aria-label="智能体范围筛选">
      <button
        v-for="option in visibilityFilterOptions"
        :key="option.value"
        type="button"
        :class="{ active: visibilityFilter === option.value }"
        :aria-pressed="visibilityFilter === option.value"
        @click="visibilityFilter = option.value"
      >
        {{ option.label }} <span>{{ option.count }}</span>
      </button>
    </div>

    <div v-if="!visibleAgents.length" class="agent-empty-state">
      <a-empty
        :image="false"
        :description="searchQuery || visibilityFilter !== 'all' ? '没有匹配的智能体' : '暂无智能体'"
      />
    </div>

    <template v-else>
      <section class="agent-group-section">
        <ExtensionCardGrid :min-width="320">
          <InfoCard
            v-for="agent in visibleAgents"
            :key="agent.agent_id"
            :title="agent.name"
            :subtitle="agent.agent_id"
            :description="agent.description || '暂无描述'"
            :default-icon="Bot"
            :tags="[{ name: getAgentShareLabel(agent), color: 'gray' }]"
            class="config-card agent-card"
            @click="canManageAgent(agent) && openEditAgentModal(agent)"
          >
            <template #icon>
              <FallbackAvatar
                class="agent-card-icon-image"
                :src="agent.icon"
                :name="agent.name || agent.agent_id"
                :seed="agent.agent_id || agent.name"
                kind="agent"
                :size="40"
                shape="rounded"
                :alt="`${agent.name || '智能体'}图标`"
              />
            </template>

            <template v-if="canManageAgent(agent)" #card-more-action-corner>
              <a-menu>
                <a-menu-item key="edit" @click.stop="openEditAgentModal(agent)">
                  <span class="lucide-menu-item">
                    <SquarePen :size="14" />
                    <span>编辑智能体</span>
                  </span>
                </a-menu-item>
                <a-menu-item
                  key="delete"
                  :disabled="isBuiltinAgent(agent)"
                  :danger="!isBuiltinAgent(agent)"
                  @click.stop="deleteAgent(agent)"
                >
                  <span class="lucide-menu-item">
                    <Trash2 :size="14" />
                    <span>删除智能体</span>
                  </span>
                </a-menu-item>
              </a-menu>
            </template>

            <template #tag-actions>
              <a-button
                v-if="agent.can_run"
                type="text"
                size="small"
                class="agent-chat-entry"
                @click.stop="openAgentChat(agent)"
              >
                去对话
                <ChevronRight :size="14" />
              </a-button>
            </template>
          </InfoCard>
        </ExtensionCardGrid>
      </section>
    </template>

    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="handleAgentSaved"
    />
  </div>
</template>

<style lang="less" scoped>
.agent-manage-panel {
  height: 100%;
  min-height: 0;
}

.agent-empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 100px 20px;
  text-align: center;
}

.agent-group-section + .agent-group-section {
  padding-top: 2px;
}

.agent-filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px var(--page-padding) 0;
  flex-wrap: wrap;

  button {
    display: inline-flex;
    min-height: 32px;
    padding: 0 12px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--gray-500);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    align-items: center;
    gap: 5px;

    &:hover,
    &.active {
      background: var(--gray-50);
      color: var(--gray-900);
    }

    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: 2px;
    }

    span {
      color: var(--gray-400);
      font-variant-numeric: tabular-nums;
    }
  }
}

.agent-card-icon-image {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.agent-card :deep(.info-card-tags) {
  justify-content: flex-start;
  margin-top: auto;
}

.agent-chat-entry {
  display: inline-flex;
  align-items: center;
  min-width: auto;
  height: 24px;
  padding: 0 2px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  box-shadow: none;
  color: var(--gray-600);
  font-size: 12px;
  gap: 4px;

  &:hover {
    border: 0;
    background: transparent;
    box-shadow: none;
    color: var(--main-700);
  }

  &:focus:not(:focus-visible) {
    outline: none;
  }

  &:focus-visible {
    outline: 2px solid var(--main-200);
    outline-offset: 2px;
  }
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
