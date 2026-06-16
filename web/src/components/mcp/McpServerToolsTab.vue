<template>
  <div class="tab-content tools-tab">
    <div class="tools-toolbar">
      <a-input-search
        v-model:value="toolSearchText"
        placeholder="搜索工具..."
        style="width: 200px"
        allowClear
      />
      <a-button @click="fetchTools" :loading="toolsLoading" class="lucide-icon-btn">
        <RotateCw :size="14" />
        <span>刷新</span>
      </a-button>
    </div>
    <a-spin :spinning="toolsLoading">
      <div v-if="filteredTools.length === 0" class="empty-tools">
        <a-empty :description="toolsError || '暂无工具'" />
      </div>
      <div v-else class="tools-list">
        <div
          v-for="tool in filteredTools"
          :key="tool.name"
          class="tool-card"
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
                :loading="toggleToolLoading === tool.name"
                size="small"
                @change="handleToggleTool(tool)"
              />
              <a-tooltip title="复制工具名称">
                <a-button
                  type="text"
                  size="small"
                  class="lucide-icon-btn"
                  @click="copyToolName(tool.name)"
                >
                  <Copy :size="14" />
                </a-button>
              </a-tooltip>
            </div>
          </div>
          <div v-if="tool.description" class="tool-description">
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
                    <span v-if="tool.required?.includes(paramName)" class="param-required">
                      必填
                    </span>
                    <span class="param-type">{{ param.type || 'any' }}</span>
                  </div>
                  <div v-if="param.description" class="param-desc">
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

<script setup>
import { computed, shallowRef, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Copy, Info, RotateCw } from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'

const props = defineProps({
  server: { type: Object, default: null },
  active: { type: Boolean, default: false }
})

const toolsCount = defineModel('count', { type: Number, default: 0 })

const tools = shallowRef([])
const toolsLoading = shallowRef(false)
const toolsError = shallowRef(null)
const toolSearchText = shallowRef('')
const toggleToolLoading = shallowRef(null)

const filteredTools = computed(() => {
  if (!toolSearchText.value) return tools.value
  const search = toolSearchText.value.toLowerCase()
  return tools.value.filter(
    (tool) =>
      tool.name.toLowerCase().includes(search) ||
      (tool.description && tool.description.toLowerCase().includes(search))
  )
})

const fetchTools = async () => {
  if (!props.server) return
  try {
    toolsLoading.value = true
    toolsError.value = null
    const result = await mcpApi.getMcpServerTools(props.server.name)
    if (result.success) {
      tools.value = result.data || []
      toolsCount.value = tools.value.length
    } else {
      toolsError.value = result.message || '获取工具列表失败'
      tools.value = []
      toolsCount.value = 0
    }
  } catch (err) {
    toolsError.value = err.message || '获取工具列表失败'
    tools.value = []
    toolsCount.value = 0
  } finally {
    toolsLoading.value = false
  }
}

const handleToggleTool = async (tool) => {
  if (!props.server) return
  try {
    toggleToolLoading.value = tool.name
    const result = await mcpApi.toggleMcpServerTool(props.server.name, tool.name)
    if (result.success) {
      message.success(result.message)
      const targetTool = tools.value.find((item) => item.name === tool.name)
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

watch(
  () => [props.active, props.server?.name],
  ([active]) => {
    if (active && props.server) {
      fetchTools()
    }
  },
  { immediate: true }
)
</script>

<style lang="less" scoped>
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
    gap: 12px;
  }

  .tool-card {
    background: var(--gray-0);
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    padding: 12px 16px;
    transition: border-color 0.2s ease;

    &:hover {
      border-color: var(--gray-200);
    }

    &.disabled {
      opacity: 0.6;
    }
  }

  .tool-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }

  .tool-info {
    display: flex;
    align-items: center;
    gap: 8px;
  }

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

  .tool-actions {
    display: flex;
    align-items: center;
    gap: 8px;
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
  }

  .param-item {
    background: var(--gray-50);
    padding: 8px 12px;
    border-radius: 4px;
  }

  .param-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }

  .param-name {
    font-weight: 500;
    font-size: 13px;
    color: var(--gray-900);
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
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
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  }

  .param-desc {
    font-size: 12px;
    color: var(--gray-600);
    line-height: 1.4;
  }
}
</style>
