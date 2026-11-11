<template>
  <ExtensionDetailLayout
    v-model:active-key="detailTab"
    :tabs="mcpDetailTabs"
    :loading="loading"
    :ready="Boolean(server)"
    empty-description="未找到连接器"
    class="mcp-detail"
  >
    <template #breadcrumb>
      <nav class="extension-detail-breadcrumb" aria-label="连接器详情导航">
        <button type="button" class="extension-detail-back" @click="goBack">连接器</button>
        <ChevronRight :size="15" aria-hidden="true" />
        <span class="extension-detail-current" :title="server?.name || slug">
          {{ server?.name || slug }}
        </span>
      </nav>
    </template>

    <template #actions>
      <div class="extension-detail-actions">
        <a-space :size="8">
          <a-button
            type="text"
            :aria-label="testLoading ? '正在测试连接器' : '测试连接器'"
            @click="handleTestServer"
            :disabled="testLoading"
            title="测试连接器"
            class="lucide-icon-btn extension-detail-action"
          >
            <Zap :size="14" v-if="!testLoading" />
            <span>测试</span>
          </a-button>
          <a-button
            type="text"
            aria-label="编辑连接器"
            @click="startEdit"
            :disabled="isEditing || !server || server.is_builtin"
            :title="server?.is_builtin ? '系统内置连接器的连接配置由代码管理' : '编辑连接器'"
            class="lucide-icon-btn extension-detail-action"
          >
            <Pencil :size="14" />
            <span>编辑</span>
          </a-button>
          <a-button
            type="text"
            :danger="server?.enabled !== false"
            :aria-label="`${actionLabel}连接器`"
            :title="`${actionLabel}连接器`"
            @click="handleSetServerEnabled(server, server.enabled === false)"
            class="lucide-icon-btn extension-detail-action"
          >
            <Plus v-if="server?.enabled === false" :size="14" />
            <Check v-else :size="14" />
            <span>{{ actionLabel }}</span>
          </a-button>
          <a-button v-if="server && !server.is_builtin" type="text" danger
            aria-label="删除连接器" @click="confirmDeleteServer(server)"
            class="lucide-icon-btn extension-detail-action">
            <Trash2 :size="14" /><span>删除</span>
          </a-button>
        </a-space>
      </div>
    </template>

    <template #panel-general>
      <div class="extension-detail-view mcp-general-view">
        <McpFormModal v-model:open="isEditing" :edit-mode="true" :edit-data="server" @submitted="fetchServer" />
        <McpConnectionControls :server="server" @changed="fetchServer" />

        <section class="mcp-info-section" aria-label="连接信息">
          <h2>连接信息</h2>
          <dl class="mcp-info-list">
            <div v-if="server.description" class="mcp-info-row">
              <dt>描述</dt>
              <dd>{{ server.description }}</dd>
            </div>
            <div class="mcp-info-row">
              <dt>认证方式</dt>
              <dd>{{ mcpAuthLabel(server.auth_mode) }}</dd>
            </div>
            <div class="mcp-info-row">
              <dt>传输类型</dt>
              <dd><code>{{ server.transport }}</code></dd>
            </div>
            <template v-if="server.transport === 'streamable_http' || server.transport === 'sse'">
              <div v-if="server.url" class="mcp-info-row">
                <dt>服务地址</dt>
                <dd><code>{{ server.url }}</code></dd>
              </div>
              <div v-if="server.headers && Object.keys(server.headers).length > 0" class="mcp-info-row">
                <dt>请求头</dt>
                <dd><pre class="code-pre">{{ JSON.stringify(server.headers, null, 2) }}</pre></dd>
              </div>
              <div v-if="server.timeout" class="mcp-info-row">
                <dt>HTTP 超时</dt>
                <dd>{{ server.timeout }} 秒</dd>
              </div>
              <div v-if="server.sse_read_timeout" class="mcp-info-row">
                <dt>SSE 读取超时</dt>
                <dd>{{ server.sse_read_timeout }} 秒</dd>
              </div>
            </template>
          </dl>
        </section>

        <section class="mcp-info-section mcp-maintenance-section" aria-label="维护信息">
          <h2>维护信息</h2>
          <dl class="mcp-maintenance-list">
            <div>
              <dt>创建人</dt>
              <dd>{{ server.created_by || '—' }}</dd>
            </div>
            <div>
              <dt>创建时间</dt>
              <dd>{{ formatTime(server.created_at) }}</dd>
            </div>
            <div>
              <dt>更新时间</dt>
              <dd>{{ formatTime(server.updated_at) }}</dd>
            </div>
          </dl>
        </section>
      </div>
    </template>

    <template #panel-tools>
      <div class="extension-detail-view extension-detail-gray-switches tools-tab">
        <div class="tools-toolbar">
          <a-input-search
            v-model:value="toolSearchText"
            placeholder="搜索工具..."
            style="width: 200px"
            allowClear
          />
          <a-button
            size="small"
            @click="fetchTools"
            :loading="toolsLoading"
            class="lucide-icon-btn"
          >
            <RotateCw v-if="!toolsLoading" :size="14" />
            <span>刷新</span>
          </a-button>
        </div>
        <a-spin :spinning="toolsLoading">
          <div v-if="filteredTools.length === 0" class="empty-tools">
            <a-empty :description="toolsError || '暂无工具'" />
          </div>
          <div v-else class="tools-list extension-detail-divider-list">
            <div
              v-for="tool in filteredTools"
              :key="tool.name"
              class="tool-card extension-detail-divider-row"
              :class="{ disabled: !tool.enabled }"
            >
              <div class="tool-header">
                <div class="tool-info">
                  <span class="tool-name">{{ tool.name }}</span>
                  <a-tooltip :title="`ID: ${tool.id}`">
                    <Info :size="14" class="info-icon" />
                  </a-tooltip>
                </div>
                <div class="tool-actions">
                  <a-switch
                    :checked="tool.enabled"
                    :aria-label="`${tool.name} ${tool.enabled ? '已启用' : '已禁用'}`"
                    @change="handleToggleTool(tool)"
                    :loading="toggleToolLoading === tool.name"
                    size="small"
                  />
                  <a-tooltip title="复制工具名称">
                    <a-button
                      type="text"
                      size="small"
                      :aria-label="`复制工具名称 ${tool.name}`"
                      @click="copyToolName(tool.name)"
                      class="lucide-icon-btn"
                    >
                      <Copy :size="14" />
                    </a-button>
                  </a-tooltip>
                </div>
              </div>
              <div class="tool-description" v-if="tool.description">
                {{ tool.description }}
              </div>
              <a-collapse v-if="tool.parameters && Object.keys(tool.parameters).length > 0" ghost>
                <a-collapse-panel key="params" header="参数">
                  <div class="params-list">
                    <div
                      v-for="(param, paramName) in tool.parameters"
                      :key="paramName"
                      class="param-item"
                    >
                      <div class="param-header">
                        <span class="param-name">{{ paramName }}</span>
                        <span class="param-required" v-if="tool.required?.includes(paramName)"
                          >必填</span
                        >
                        <span class="param-type">{{ param.type || 'any' }}</span>
                      </div>
                      <div class="param-desc" v-if="param.description">
                        {{ param.description }}
                      </div>
                    </div>
                  </div>
                </a-collapse-panel>
              </a-collapse>
            </div>
          </div>
        </a-spin>
      </div>
    </template>
  </ExtensionDetailLayout>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  Check,
  ChevronRight,
  Zap,
  Pencil,
  Trash2,
  Plus,
  RotateCw,
  Info,
  Copy,
  Settings2,
  Wrench
} from '@lucide/vue'
import McpFormModal from './McpFormModal.vue'
import McpConnectionControls from './McpConnectionControls.vue'
import ExtensionDetailLayout from '@/shared/ui/ExtensionDetailLayout.vue'
import { mcpApi } from '@/apis/mcp_api'
import { mcpAuthLabel } from '@/modules/extensions/model/mcpOAuth'
import { formatFullDateTime } from '@/shared/lib/time'

const route = useRoute()
const router = useRouter()
const slug = computed(() => decodeURIComponent(route.params.slug ?? route.params.name))

const loading = ref(false)
const server = ref(null)
const detailTab = ref('general')
const testLoading = ref(null)

const tools = ref([])
const toolsLoading = ref(false)
const toolsError = ref(null)
const toolSearchText = ref('')
const toggleToolLoading = ref(null)

const mcpDetailTabs = [
  { key: 'general', label: '信息', icon: Settings2 },
  { key: 'tools', label: '工具', icon: Wrench }
]

const isEditing = ref(false)
const actionLabel = computed(() => {
  return server.value?.enabled === false ? '启用' : '停用'
})

const filteredTools = computed(() => {
  if (!toolSearchText.value) return tools.value
  const search = toolSearchText.value.toLowerCase()
  return tools.value.filter(
    (t) =>
      t.name.toLowerCase().includes(search) ||
      (t.description && t.description.toLowerCase().includes(search))
  )
})

const goBack = () => {
  router.push({ path: '/extensions', query: { tab: 'mcp' } })
}

const formatTime = (timeStr) => formatFullDateTime(timeStr)

const startEdit = () => {
  if (server.value) isEditing.value = true
}

const fetchServer = async () => {
  try {
    loading.value = true
    const result = await mcpApi.getMcpServer(slug.value)
    if (result.success) {
      server.value = result.data
    } else {
      message.error(result.message || '获取连接器详情失败')
    }
  } catch (err) {
    message.error(err.message || '获取连接器详情失败')
  } finally {
    loading.value = false
  }
}

const fetchTools = async () => {
  if (!server.value) return
  try {
    toolsLoading.value = true
    toolsError.value = null
    const result = await mcpApi.getMcpServerTools(server.value.slug)
    if (result.success) {
      tools.value = result.data || []
    } else {
      toolsError.value = result.message || '获取工具列表失败'
      tools.value = []
    }
  } catch (err) {
    toolsError.value = err.message || '获取工具列表失败'
    tools.value = []
  } finally {
    toolsLoading.value = false
  }
}

const handleToggleTool = async (tool) => {
  if (!server.value) return
  try {
    toggleToolLoading.value = tool.name
    const result = await mcpApi.toggleMcpServerTool(server.value.slug, tool.name)
    if (result.success) {
      message.success(result.message)
      const targetTool = tools.value.find((t) => t.name === tool.name)
      if (targetTool) targetTool.enabled = result.enabled
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    toggleToolLoading.value = null
  }
}

const copyToolName = async (toolName) => {
  try {
    await navigator.clipboard.writeText(toolName)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

const handleTestServer = async () => {
  if (!server.value) return
  try {
    testLoading.value = server.value.name
    const result = await mcpApi.testMcpServer(server.value.slug)
    if (result.success) {
      message.success(result.message)
    } else {
      message.warning(result.message || '连接失败')
    }
  } catch (err) {
    message.error(err.message || '测试失败')
  } finally {
    testLoading.value = null
  }
}

const handleSetServerEnabled = async (srv, enabled) => {
  try {
    const result = await mcpApi.updateMcpServerStatus(srv.slug, enabled)
    if (result.success) {
      message.success(`连接器已${enabled ? '启用' : '停用'}`)
      await fetchServer()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  }
}

const confirmDeleteServer = (srv) => {
  Modal.confirm({
    title: '确认删除连接器',
    content: `确定要删除连接器 "${srv.name}" 吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const result = await mcpApi.deleteMcpServer(srv.slug)
        if (result.success) {
          message.success('连接器删除成功')
          router.push({ path: '/extensions', query: { tab: 'mcp' } })
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (err) {
        message.error(err.message || '删除失败')
      }
    }
  })
}

watch(detailTab, (tab) => {
  if (tab === 'tools' && server.value) {
    fetchTools()
  }
})

onMounted(() => {
  fetchServer()
})
</script>

<style lang="less" scoped>

.mcp-general-view {
  display: flex;
  flex-direction: column;
  gap: 28px;

  h2 {
    margin: 0;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
    line-height: 22px;
  }

  dl { margin: 0; }
  dt { color: var(--gray-500); font-size: 12px; }
  dd { min-width: 0; margin: 0; color: var(--gray-800); font-size: 13px; line-height: 22px; overflow-wrap: anywhere; }
  code { font-size: 12px; }
}

.mcp-info-section h2 { margin-bottom: 14px; }

.mcp-info-list {
  display: grid;
  gap: 14px;
}

.mcp-info-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  align-items: baseline;
  gap: 16px;

  .code-pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
}

.mcp-maintenance-section {
  padding-top: 20px;
  border-top: 1px solid var(--gray-150);
}

.mcp-maintenance-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;

  dd { margin-top: 4px; }
}

/* 工具列表样式 */
.tools-tab {
  .tools-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }

  .empty-tools {
    padding: 40px 0;
  }

  .tools-list {
    display: flex;
    flex-direction: column;
    gap: 0;

    .tool-card {
      background: transparent;
      border: 0;
      border-bottom: 1px solid var(--gray-150);
      border-radius: 0;
      padding: 16px 0;

      &.disabled {
        opacity: 0.6;
      }

      .tool-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;

        .tool-info {
          display: flex;
          align-items: center;
          gap: 8px;

          .tool-name {
            font-weight: 600;
            font-size: 14px;
            color: var(--gray-900);
          }

          .info-icon {
            color: var(--gray-400);
            cursor: pointer;
            &:hover {
              color: var(--gray-600);
            }
          }
        }

        .tool-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }
      }

      .tool-description {
        font-size: 13px;
        color: var(--gray-600);
        line-height: 1.4;
        margin-bottom: 8px;
      }

      :deep(.ant-collapse) {
        background: transparent;
        border: none;

        .ant-collapse-header {
          padding: 8px 0;
          font-size: 13px;
          color: var(--gray-600);
        }
        .ant-collapse-content-box {
          padding: 0;
        }
      }

      .params-list {
        display: flex;
        flex-direction: column;
        gap: 8px;

        .param-item {
          background: var(--gray-50);
          padding: 8px 12px;
          border-radius: 4px;

          .param-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;

            .param-name {
              font-weight: 500;
              font-size: 13px;
              color: var(--gray-900);
              font-family: 'Monaco', 'Consolas', monospace;
            }
            .param-required {
              font-size: 11px;
              color: var(--color-error-500);
              background: var(--color-error-50);
              padding: 1px 6px;
              border-radius: 3px;
            }
            .param-type {
              font-size: 11px;
              color: var(--gray-500);
              background: var(--gray-100);
              padding: 1px 6px;
              border-radius: 3px;
              font-family: 'Monaco', 'Consolas', monospace;
            }
          }

          .param-desc {
            font-size: 12px;
            color: var(--gray-600);
            line-height: 1.4;
          }
        }
      }
    }
  }
}

@media (max-width: 768px) {
  .mcp-info-row { grid-template-columns: 76px minmax(0, 1fr); gap: 12px; }
  .mcp-maintenance-list { grid-template-columns: 1fr; gap: 12px; }
  .mcp-maintenance-list > div { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 12px; }
  .mcp-maintenance-list dd { margin-top: 0; }

  .tools-toolbar {
    gap: 8px;

    :deep(.ant-input-search) {
      min-width: 0;
      flex: 1;
    }
  }
}
</style>
