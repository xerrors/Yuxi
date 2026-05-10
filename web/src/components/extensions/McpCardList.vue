<template>
  <div class="mcp-cards-page extension-page-root">
    <PageShoulder search-placeholder="搜索 MCP..." v-model:search="searchQuery">
      <template #actions>
        <a-button type="primary" @click="handleMcpAdd" class="lucide-icon-btn">
          <Plus :size="14" />
          <span>添加 MCP</span>
        </a-button>
        <a-tooltip title="刷新 MCP">
          <a-button class="lucide-icon-btn" :disabled="loading" @click="fetchServers">
            <RefreshCw :size="14" />
          </a-button>
        </a-tooltip>
      </template>
    </PageShoulder>

    <div
      v-if="filteredEnabledServers.length === 0 && filteredDisabledServers.length === 0"
      class="extension-card-grid-empty-state"
    >
      <a-empty
        :image="false"
        :description="searchQuery ? '无匹配 MCP' : '暂无 MCP，点击上方按钮添加'"
      />
    </div>

    <template v-else>
      <div v-if="filteredEnabledServers.length" class="extension-section-header">已添加</div>
      <ExtensionCardGrid>
        <InfoCard
          v-for="server in filteredEnabledServers"
          :key="server.name"
          :title="server.name"
          :subtitle="server.transport"
          :description="server.description || '暂无描述'"
          :tags="mcpTags(server)"
          :status="{ label: '已添加', level: 'success' }"
          @click="navigateToDetail(server)"
        >
          <template #icon>
            <span class="info-card-emoji-icon">{{ server.icon || '🔌' }}</span>
          </template>
        </InfoCard>
      </ExtensionCardGrid>

      <div v-if="filteredDisabledServers.length" class="extension-section-header">可添加</div>
      <ExtensionCardGrid v-if="filteredDisabledServers.length">
        <InfoCard
          v-for="server in filteredDisabledServers"
          :key="server.name"
          :title="server.name"
          :subtitle="server.transport"
          :description="server.description || '暂无描述'"
          :tags="mcpTags(server)"
          action-label="添加"
          @click="navigateToDetail(server)"
          @action-click="handleSetServerEnabled(server, true)"
        >
          <template #icon>
            <span class="info-card-emoji-icon">{{ server.icon || '🔌' }}</span>
          </template>
        </InfoCard>
      </ExtensionCardGrid>
    </template>

    <McpFormModal
      v-model:open="formModalVisible"
      :edit-mode="false"
      @submitted="handleFormSubmitted"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Plus, RefreshCw } from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import McpFormModal from './McpFormModal.vue'

const router = useRouter()

const loading = ref(false)
const servers = ref([])
const searchQuery = ref('')
const formModalVisible = ref(false)

const filteredServers = computed(() => {
  const sorted = [...servers.value].sort((a, b) =>
    String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN', {
      sensitivity: 'base',
      numeric: true
    })
  )
  if (!searchQuery.value) return sorted
  const q = searchQuery.value.toLowerCase()
  return sorted.filter(
    (s) => s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
  )
})

const filteredEnabledServers = computed(() =>
  filteredServers.value.filter((item) => !!item.enabled)
)
const filteredDisabledServers = computed(() =>
  filteredServers.value.filter((item) => !item.enabled)
)

const getTransportColor = (transport) => {
  const colors = { sse: 'orange', stdio: 'green', streamable_http: 'blue' }
  return colors[transport] || 'blue'
}

const mcpTags = (server) => {
  const tags = []
  if (server.created_by === 'system') tags.push({ name: '内置' })
  if (server.transport)
    tags.push({ name: server.transport, color: getTransportColor(server.transport) })
  return tags
}

const navigateToDetail = (server) => {
  router.push({ path: `/extensions/mcp/${encodeURIComponent(server.name)}` })
}

const handleMcpAdd = () => {
  formModalVisible.value = true
}

const handleFormSubmitted = async () => {
  formModalVisible.value = false
  await fetchServers()
}

const handleSetServerEnabled = async (server, enabled) => {
  try {
    const result = await mcpApi.updateMcpServerStatus(server.name, enabled)
    if (result.success) {
      message.success(result.message || `MCP 已${enabled ? '添加' : '移除'}`)
      await fetchServers()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  }
}

const fetchServers = async () => {
  try {
    loading.value = true
    const result = await mcpApi.getMcpServers()
    if (result.success) {
      servers.value = result.data || []
    }
  } catch (err) {
    message.error(err.message || '获取 MCP 列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchServers()
})

defineExpose({ fetchServers, loading })
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.info-card-emoji-icon {
  font-size: 18px;
  line-height: 1;
}
</style>
