<script setup>
import { computed, onMounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  Plus,
  RefreshCw,
  Trash2,
  SquarePen,
  Bot,
  MoreVertical
} from 'lucide-vue-next'

import { agentApi } from '@/apis/agent_api'
import AgentEditModal from '@/components/model-management/AgentEditModal.vue'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import ExtensionCardGrid from '@/components/extensions/ExtensionCardGrid.vue'
import { generatePixelAvatar } from '@/utils/pixelAvatar'

const agentStore = useAgentStore()
const agentLoading = ref(false)
const searchQuery = ref('')

const agentBackendOptions = ref([])
const managedAgents = ref([])
const agentEditModalRef = ref(null)

const normalizeAgent = (agent) => {
  const agentId = agent?.agent_id || agent?.slug || agent?.id
  return agentId
    ? { ...agent, id: agentId, agent_id: agentId, slug: agent?.slug || agentId }
    : agent
}

const filteredAgents = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  const list = managedAgents.value || []
  const filtered = keyword
    ? list.filter(
        (agent) =>
          String(agent.name || '')
            .toLowerCase()
            .includes(keyword) ||
          String(agent.id || '')
            .toLowerCase()
            .includes(keyword) ||
          String(agent.backend_id || '')
            .toLowerCase()
            .includes(keyword)
      )
    : list
  return [...filtered].sort((a, b) => {
    if (isBuiltinAgent(a) !== isBuiltinAgent(b)) return isBuiltinAgent(a) ? -1 : 1
    return String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-CN')
  })
})

const groupedAgents = computed(() => {
  const agents = filteredAgents.value.filter((agent) => !agent.is_subagent)
  const subagents = filteredAgents.value.filter((agent) => agent.is_subagent)
  return [
    { key: 'agents', title: '智能体', agents },
    { key: 'subagents', title: '子智能体', agents: subagents }
  ].filter((group) => group.agents.length > 0)
})

const agentStats = computed(() => ({
  total: managedAgents.value.length,
  builtin: managedAgents.value.filter(isBuiltinAgent).length,
  manageable: managedAgents.value.filter((agent) => agent.can_manage).length,
  global: managedAgents.value.filter((agent) => agent.share_config?.access_level === 'global')
    .length
}))
const canManageAgent = (agent) => !!agent?.can_manage
const getAgentDefaultIconSrc = (agent) => (agent.id ? generatePixelAvatar(agent.id) : '')
const getAgentTags = (agent) => [
  ...(!agent?.can_manage ? [{ name: '只读', color: 'default' }] : []),
  ...(agent?.backend_id ? [{ name: agent.backend_id, color: 'blue' }] : [])
]

// ============ Agent Operations ============
const loadAgentBackends = async () => {
  try {
    const response = await agentApi.getAgentBackends()
    agentBackendOptions.value = (response.backends || []).map((backend) => ({
      label: backend.name || backend.backend_id,
      value: backend.backend_id
    }))
  } catch (error) {
    message.error(error.message || '加载智能体后端失败')
  }
}

const loadAgents = async () => {
  agentLoading.value = true
  try {
    const response = await agentApi.getAgents({ includeSubagents: true })
    managedAgents.value = (response.agents || []).map(normalizeAgent)
  } catch (error) {
    message.error(error.message || '加载智能体失败')
  } finally {
    agentLoading.value = false
  }
}

const openCreateAgentModal = () => {
  agentEditModalRef.value?.openCreate()
}

const openEditAgentModal = (agent) => {
  if (!canManageAgent(agent)) return
  agentEditModalRef.value?.openEdit(agent)
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
        await agentApi.deleteAgent(agent.id)
        await refreshAgentLists()
        message.success('智能体已删除')
      } catch (error) {
        message.error(error.message || '删除智能体失败')
      }
    }
  })
}

onMounted(async () => {
  await Promise.all([loadAgentBackends(), loadAgents()])
})

defineExpose({
  loading: agentLoading,
  stats: agentStats,
  refresh: loadAgents
})
</script>

<template>
  <div class="agent-manage-panel">
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索智能体...">
      <template #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreateAgentModal">
          <Plus :size="14" />
          新增智能体
        </a-button>
        <a-button class="lucide-icon-btn" @click="loadAgents" :loading="agentLoading">
          <RefreshCw :size="14" :class="{ spinning: agentLoading }" />
        </a-button>
      </template>
    </PageShoulder>

    <div v-if="groupedAgents.length === 0" class="agent-empty-state">
      <a-empty :image="false" :description="searchQuery ? '没有匹配的智能体' : '暂无智能体'" />
    </div>

    <template v-else>
      <section v-for="group in groupedAgents" :key="group.key" class="agent-group-section">
        <div class="agent-group-header">
          <span>{{ group.title }}</span>
        </div>
        <ExtensionCardGrid :min-width="320">
          <InfoCard
            v-for="agent in group.agents"
            :key="agent.id"
            :title="agent.name"
            :subtitle="agent.slug || agent.id"
            :description="agent.description || '暂无描述'"
            :default-icon="Bot"
            :tags="getAgentTags(agent)"
            class="config-card agent-card"
            @click="canManageAgent(agent) && openEditAgentModal(agent)"
          >
            <template #icon>
              <FallbackAvatar
                class="agent-card-icon-image"
                :src="agent.icon"
                :default-src="getAgentDefaultIconSrc(agent)"
                :name="agent.name || agent.id"
                :seed="agent.id || agent.name"
                kind="agent"
                :size="40"
                shape="rounded"
                :alt="`${agent.name || '智能体'}图标`"
              />
            </template>

            <template #status>
              <a-dropdown v-if="canManageAgent(agent)" :trigger="['click']" placement="bottomRight">
                <template #overlay>
                  <a-menu>
                    <a-menu-item key="edit" @click.stop="openEditAgentModal(agent)">
                      <span class="agent-card-menu-item">
                        <SquarePen :size="14" />
                        编辑智能体
                      </span>
                    </a-menu-item>
                    <a-menu-item
                      key="delete"
                      :disabled="isBuiltinAgent(agent)"
                      @click.stop="deleteAgent(agent)"
                    >
                      <span
                        class="agent-card-menu-item"
                        :class="{ danger: !isBuiltinAgent(agent) }"
                      >
                        <Trash2 :size="14" />
                        删除智能体
                      </span>
                    </a-menu-item>
                  </a-menu>
                </template>
                <a-button
                  type="text"
                  size="small"
                  class="agent-card-menu-trigger"
                  aria-label="智能体操作"
                  @click.stop
                >
                  <MoreVertical :size="16" />
                </a-button>
              </a-dropdown>
            </template>
          </InfoCard>
        </ExtensionCardGrid>
      </section>
    </template>

    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="refreshAgentLists"
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

.agent-group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px var(--page-padding) 0;
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.4px;
  line-height: 18px;
}

.agent-card-icon-image {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.agent-card-menu-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  color: var(--gray-600);

  &:hover,
  &:focus {
    color: var(--gray-700);
    background: var(--gray-50);
  }
}

.agent-card-menu-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;

  &.danger {
    color: var(--color-error-700);
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
