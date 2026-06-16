<template>
  <div class="mcp-detail extension-detail-page">
    <div v-if="loading" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>
    <div class="detail-top-bar">
      <button class="detail-back-btn" @click="goBack">
        <ArrowLeft :size="16" />
        <span>返回</span>
      </button>
      <div class="detail-title-area">
        <span class="detail-icon">{{ server?.icon || '🔌' }}</span>
        <div class="detail-title-text">
          <h2>{{ server?.name || name }}</h2>
          <span class="detail-subtitle">{{ server?.transport || '' }}</span>
        </div>
      </div>
      <div class="detail-actions">
        <a-space :size="8">
          <button
            type="button"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
            :disabled="testLoading"
            @click="handleTestServer"
          >
            <Zap v-if="!testLoading" :size="14" />
            <span>测试</span>
          </button>
          <button
            type="button"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
            :disabled="isEditing || !server"
            @click="startEdit"
          >
            <Pencil :size="14" />
            <span>编辑</span>
          </button>
          <button
            type="button"
            :class="[
              'lucide-icon-btn',
              'extension-panel-action',
              server?.enabled === false
                ? 'extension-panel-action-primary'
                : 'extension-panel-action-danger'
            ]"
            @click="handleDangerAction"
          >
            <Plus v-if="server?.enabled === false" :size="14" />
            <Trash2 v-else :size="14" />
            <span>{{ actionLabel }}</span>
          </button>
        </a-space>
      </div>
    </div>

    <div class="detail-content-wrapper">
      <a-spin :spinning="loading">
        <div v-if="server" class="detail-content-inner">
          <a-tabs v-model:activeKey="detailTab" class="detail-tabs">
            <a-tab-pane key="general">
              <template #tab>
                <span class="tab-title"><Settings2 :size="14" />信息</span>
              </template>
              <McpServerGeneralTab
                v-model:editing="isEditing"
                :server="server"
                @saved="fetchServer"
              />
            </a-tab-pane>

            <a-tab-pane key="tools">
              <template #tab>
                <span class="tab-title"><Wrench :size="14" />工具 ({{ toolsCount }})</span>
              </template>
              <McpServerToolsTab
                v-model:count="toolsCount"
                :server="server"
                :active="detailTab === 'tools'"
              />
            </a-tab-pane>

            <a-tab-pane key="connections">
              <template #tab>
                <span class="tab-title"><KeyRound :size="14" />连接 ({{ connectionsCount }})</span>
              </template>
              <McpServerConnectionsTab
                v-model:count="connectionsCount"
                :server="server"
                :active="detailTab === 'connections'"
              />
            </a-tab-pane>
          </a-tabs>
        </div>
        <div v-else-if="!loading" class="detail-empty">
          <a-empty description="未找到 MCP 服务器" />
        </div>
      </a-spin>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  ArrowLeft,
  KeyRound,
  Pencil,
  Plus,
  Settings2,
  Trash2,
  Wrench,
  Zap
} from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'
import McpServerConnectionsTab from '@/components/mcp/McpServerConnectionsTab.vue'
import McpServerGeneralTab from '@/components/mcp/McpServerGeneralTab.vue'
import McpServerToolsTab from '@/components/mcp/McpServerToolsTab.vue'

const route = useRoute()
const router = useRouter()
const name = computed(() => decodeURIComponent(route.params.name))

const loading = shallowRef(false)
const server = shallowRef(null)
const detailTab = shallowRef('general')
const testLoading = shallowRef(null)
const isEditing = shallowRef(false)
const toolsCount = shallowRef(0)
const connectionsCount = shallowRef(0)

const actionLabel = computed(() => {
  if (server.value?.enabled === false) return '恢复'
  return server.value?.created_by === 'system' ? '移除' : '退役'
})

const goBack = () => {
  router.push({ path: '/extensions', query: { tab: 'mcp' } })
}

const startEdit = () => {
  if (!server.value) return
  detailTab.value = 'general'
  isEditing.value = true
}

const fetchServer = async () => {
  try {
    loading.value = true
    const result = await mcpApi.getMcpServer(name.value)
    if (result.success) {
      server.value = result.data
    } else {
      message.error(result.message || '获取 MCP 详情失败')
    }
  } catch (err) {
    message.error(err.message || '获取 MCP 详情失败')
  } finally {
    loading.value = false
  }
}

const handleTestServer = async () => {
  if (!server.value) return
  try {
    testLoading.value = server.value.name
    const result = await mcpApi.testMcpServer(server.value.name)
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

const handleDangerAction = async () => {
  if (!server.value) return
  if (server.value.enabled === false) {
    await handleSetServerEnabled(server.value, true)
    return
  }
  if (server.value.created_by === 'system') {
    await handleSetServerEnabled(server.value, false)
    return
  }
  confirmDeleteServer(server.value)
}

const handleSetServerEnabled = async (srv, enabled) => {
  try {
    const result = await mcpApi.updateMcpServerStatus(srv.name, enabled)
    if (result.success) {
      message.success(result.message || `MCP 已${enabled ? '添加' : '移除'}`)
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
    title: '确认退役 MCP',
    content: `确定要退役 MCP "${srv.name}" 吗？退役后不会再被新运行加载，但配置和连接会保留。`,
    okText: '退役',
    okType: 'primary',
    cancelText: '取消',
    async onOk() {
      try {
        const result = await mcpApi.deleteMcpServer(srv.name)
        if (result.success) {
          message.success(result.message || 'MCP 已退役')
          await fetchServer()
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (err) {
        message.error(err.message || '删除失败')
      }
    }
  })
}

onMounted(() => {
  fetchServer()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
@import '@/assets/css/extension-detail.less';

.detail-empty {
  padding: 40px 0;
}
</style>
