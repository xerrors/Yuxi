<template>
  <div class="mcp-cards-page extension-page-root">
    <PageShoulder search-placeholder="搜索连接器..." v-model:search="searchQuery">
      <template #actions>
        <a-tooltip title="刷新连接器" placement="bottom">
          <a-button
            class="yuxi-refresh-button lucide-icon-btn"
            aria-label="刷新连接器"
            :disabled="loading"
            @click="fetchServers"
          >
            <RefreshCw
              :size="14"
              class="page-shoulder-refresh-icon"
              :class="{ 'is-spinning': loading }"
            />
          </a-button>
        </a-tooltip>
        <a-button v-if="userStore.isSuperAdmin" type="primary" @click="handleMcpAdd" class="lucide-icon-btn">
          <Plus :size="14" />
          <span>新增连接器</span>
        </a-button>
      </template>
    </PageShoulder>

    <div
      v-if="visibleServerGroups.length === 0"
      class="extension-card-grid-empty-state"
    >
      <a-empty
        :image="false"
        :description="searchQuery ? '无匹配连接器' : '暂无连接器，点击上方按钮添加'"
      />
    </div>

    <template v-else>
      <template v-for="group in visibleServerGroups" :key="group.key">
        <div class="extension-section-header">{{ group.title }}</div>
        <ExtensionCardGrid :min-width="280">
          <InfoCard
            v-for="server in group.servers"
            :key="server.slug"
            :title="formatExtensionCardTitle(server.name)"
            :subtitle="server.slug"
            :description="server.description || '暂无描述'"
            :tags="mcpCardTags(server)"
            :default-icon="getSkillIcon(`${server.slug} ${server.name}`, Plug)"
            :icon-color="getExtensionIconColor(server.slug, themeStore.isDark)"
            @click="handleCardClick(server)"
          >
            <template v-if="server.can_manage" #actions>
              <button
                type="button"
                class="mcp-enabled-action"
                :class="{ enabled: !!server.enabled }"
                :disabled="isActionLoading(server)"
                :aria-label="server.enabled ? '停用连接器' : '启用连接器'"
                @click.stop="handleSetServerEnabled(server, !server.enabled)"
              >
                <Plus v-if="!server.enabled" :size="15" class="action-icon" />
                <template v-else>
                  <Check :size="15" class="action-icon action-icon-check" />
                  <Minus :size="15" class="action-icon action-icon-minus" />
                </template>
              </button>
            </template>
          </InfoCard>
        </ExtensionCardGrid>
      </template>
    </template>

    <a-modal
      v-model:open="basicInfoVisible"
      class="mcp-basic-info-modal"
      :footer="null"
      width="min(680px, calc(100vw - 32px))"
      centered
      :closable="!previewServer?.can_manage"
      :destroy-on-close="true"
      @cancel="closeBasicInfo"
    >
      <div v-if="previewServer" class="mcp-basic-info-panel">
        <div class="mcp-basic-info-header">
          <div class="mcp-basic-info-title-area">
            <div class="mcp-basic-info-icon" :style="{ color: getExtensionIconColor(previewServer.slug, themeStore.isDark) }">
              <component :is="getSkillIcon(`${previewServer.slug} ${previewServer.name}`, Plug)" :size="18" aria-hidden="true" />
            </div>
            <div class="mcp-basic-info-title-text">
              <div class="mcp-basic-info-title">
                {{ formatExtensionCardTitle(previewServer.name) }}
              </div>
              <div class="mcp-basic-info-meta">
                <span>{{ mcpAuthLabel(previewServer.auth_mode) }} · {{ previewServer.enabled ? '已启用' : '已停用' }}</span>
                <span v-if="previewServer.is_builtin" class="mcp-basic-info-tag"> 内置 </span>
              </div>
            </div>
          </div>
          <div v-if="previewServer.can_manage" class="mcp-basic-info-actions">
            <a-switch
              :checked="previewServer.enabled"
              :loading="isActionLoading(previewServer)"
              :disabled="isActionLoading(previewServer)"
              size="small"
              aria-label="启用服务"
              @change="handleSetServerEnabled(previewServer, $event)"
            />
          </div>
        </div>

        <div class="mcp-basic-info-body">
          <div class="mcp-basic-info-row">
            <label>描述</label>
            <span>{{ previewServer.description || '暂无描述' }}</span>
          </div>
          <McpConnectionControls :key="userStore.uid" :server="previewServer" @changed="refreshPreview" />
        </div>
        <div v-if="previewServer.can_manage" class="mcp-basic-info-footer">
          <a-button @click="closeBasicInfo">关闭</a-button>
          <a-button type="primary" @click="navigateToDetail(previewServer)">管理连接器</a-button>
        </div>
      </div>
    </a-modal>

    <McpFormModal
      v-model:open="formModalVisible"
      :edit-mode="false"
      @submitted="handleFormSubmitted"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Check, Minus, Plus, RefreshCw, Plug } from '@lucide/vue'
import { useUserStore } from '@/modules/identity'
import { mcpApi } from '@/apis/mcp_api'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import InfoCard from '@/shared/ui/InfoCard.vue'
import PageShoulder from '@/shared/ui/PageShoulder.vue'
import McpFormModal from './McpFormModal.vue'
import McpConnectionControls from './McpConnectionControls.vue'
import { mcpAuthLabel, mcpCardTags } from '@/modules/extensions/model/mcpOAuth'
import { formatExtensionCardTitle } from '@/modules/extensions/model/extensionDisplayName'
import { getExtensionIconColor } from '@/shared/lib/extensionIconColor'
import { getSkillIcon } from '@/shared/lib/skill_icon_utils'
import { useThemeStore } from '@/shared/model/theme'

const themeStore = useThemeStore()
const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const loading = ref(false)
const servers = ref([])
const searchQuery = ref('')
const formModalVisible = ref(false)
const basicInfoVisible = ref(false)
const previewServer = ref(null)
const actionLoadingSlug = ref('')

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

const visibleServerGroups = computed(() => [
  { key: 'enabled', title: '已启用', enabled: true, servers: filteredServers.value.filter((item) => !!item.enabled) },
  { key: 'disabled', title: '未启用', enabled: false, servers: filteredServers.value.filter((item) => !item.enabled) }
].filter((group) => group.servers.length))

const navigateToDetail = (server) => {
  if (!server.can_manage) return
  router.push({ path: `/extensions/mcp/${encodeURIComponent(server.slug)}` })
}

const handleCardClick = (server) => {
  previewServer.value = server
  basicInfoVisible.value = true
}

const closeBasicInfo = () => {
  basicInfoVisible.value = false
  previewServer.value = null
}

async function refreshPreview() {
  const slug = previewServer.value?.slug
  await fetchServers()
  previewServer.value = servers.value.find((server) => server.slug === slug) || null
  if (!previewServer.value) basicInfoVisible.value = false
}

const isActionLoading = (server) => actionLoadingSlug.value === server?.slug

const handleMcpAdd = () => {
  formModalVisible.value = true
}

const handleFormSubmitted = async () => {
  formModalVisible.value = false
  await fetchServers()
}

const handleSetServerEnabled = async (server, enabled) => {
  try {
    actionLoadingSlug.value = server.slug
    const result = await mcpApi.updateMcpServerStatus(server.slug, enabled)
    if (result.success) {
      message.success(enabled ? '服务已启用，请在智能体工具配置中绑定或通过 Skill 依赖激活' : '服务已停用')
      await refreshPreview()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    actionLoadingSlug.value = ''
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
    message.error(err.message || '获取连接器列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  const result = route.query.mcp_oauth_result
  const failed = route.query.mcp_oauth_error
  if (result || failed) {
    const query = { ...route.query }
    delete query.mcp_oauth_result
    delete query.mcp_oauth_error
    await router.replace({ path: route.path, query })
    if (result) {
      try {
        await mcpApi.complete(String(result))
        message.success('连接器账号已连接')
      } catch (err) {
        message.error(err.message || '授权结果已失效，请重新连接')
      }
    } else message.error('连接器授权未完成，请重新连接')
  }
  await fetchServers()
})

defineExpose({ fetchServers, loading })
</script>

<style lang="less" scoped>
.dark .mcp-cards-page :deep(.info-card) {
  --border-surface: transparent;
  --border-surface-hover: transparent;
}

.mcp-enabled-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-600);
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease,
    color 0.18s ease;

  &:hover,
  &:focus {
    outline: none;
    border-color: var(--gray-300);
    background: var(--gray-50);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  &.enabled {
    color: var(--color-success-700);

    .action-icon-minus {
      display: none;
    }

    &:hover,
    &:focus {
      border-color: var(--color-error-100);
      background: var(--color-error-50);
      color: var(--color-error-700);

      .action-icon-check {
        display: none;
      }

      .action-icon-minus {
        display: block;
      }
    }
  }
}

.action-icon {
  flex-shrink: 0;
}

.mcp-basic-info-panel {
  display: flex;
  flex-direction: column;
  min-height: 300px;
}

.mcp-basic-info-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.mcp-basic-info-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--gray-50);
  color: var(--gray-600);
}

.mcp-basic-info-title-area {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}

.mcp-basic-info-title-text { min-width: 0; }

.mcp-basic-info-actions {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  padding-top: 2px;
}

.mcp-basic-info-title {
  overflow: hidden;
  color: var(--gray-900);
  font-size: 16px;
  font-weight: 700;
  line-height: 22px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mcp-basic-info-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.mcp-basic-info-tag {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  font-weight: 600;
}

.mcp-basic-info-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 20px;
  max-height: min(56vh, 520px);
  overflow-y: auto;
}

.mcp-basic-info-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 12px;
  color: var(--gray-700);
  font-size: 13px;
  line-height: 20px;

  label {
    color: var(--gray-500);
    font-weight: 600;
  }

  span {
    min-width: 0;
    overflow-wrap: anywhere;
  }
}

.mcp-basic-info-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}
</style>
