<template>
  <div class="tool-call-display" :class="{ 'is-collapsed': !isExpanded }">
    <!-- Header Slot -->
    <div class="tool-header" @click="toggleExpand">
      <!-- Slot for completely overriding header (not recommended based on new requirement but kept for backward compat if needed, or remove if strict) -->
      <!-- Actually, the requirement says "tool call 的 header 也要有 slot", but "ICON 保留".
           So we should probably not use a single "header" slot that replaces everything.
           Instead, we structure it: Icon + Content + ExpandIcon.
      -->

      <!-- Fixed Status Icon -->
      <span v-if="toolCall.status === 'success' || toolCall.tool_call_result">
        <component :is="toolIcon" size="16" class="tool-loader tool-success" />
      </span>
      <span v-else-if="toolCall.status === 'error'">
        <XCircle size="16" class="tool-loader tool-error" />
      </span>
      <span v-else>
        <Loader size="16" class="tool-loader rotate tool-loading" />
      </span>

      <!-- Content Area with Slots -->
      <div class="tool-header-content">
        <!-- Generic Header Slot (Overrides specific slots if provided) -->
        <template v-if="$slots.header">
          <slot name="header" :tool-call="toolCall" :tool-name="toolName"></slot>
        </template>

        <!-- Specific State Slots (Fallback) -->
        <template v-else>
          <slot
            name="header-success"
            v-if="toolCall.status === 'success' || toolCall.tool_call_result"
            :tool-name="toolName"
            :result-content="resultContent"
          >
            工具&nbsp; <span class="tool-name">{{ toolName }}</span> &nbsp; 执行完成
          </slot>

          <slot
            name="header-error"
            v-else-if="toolCall.status === 'error'"
            :tool-name="toolName"
            :error-message="toolCall.error_message"
          >
            工具&nbsp; <span class="tool-name">{{ toolName }}</span> &nbsp; 执行失败
            <span v-if="toolCall.error_message">（{{ toolCall.error_message }}）</span>
          </slot>

          <slot name="header-running" v-else :tool-name="toolName">
            正在调用工具: &nbsp; <span class="tool-name">{{ toolName }}</span>
          </slot>
        </template>
      </div>

      <!-- Fixed Expand Icon -->
      <span class="tool-expand-icon">
        <ChevronsDownUp v-if="isExpanded" size="14" />
        <ChevronsUpDown v-else size="14" />
      </span>
    </div>

    <!-- Content Area -->
    <div class="tool-content" v-show="isExpanded">
      <!-- Params Slot -->
      <div class="tool-params" v-if="hasParams && !hideParams">
        <slot name="params" :tool-call="toolCall" :args="formattedArgs">
          <div class="tool-params-content">
            <strong>参数: </strong>
            <span>{{ formattedArgs }}</span>
          </div>
        </slot>
      </div>

      <!-- Result Slot -->
      <div class="tool-result" v-if="hasResult">
        <slot name="result" :tool-call="toolCall" :result-content="resultContent">
          <div class="tool-result-content" :data-tool-call-id="toolCall.id">
            <!-- Default rendering -->
            <div class="tool-result-renderer">
              <div class="default-result">
                <div class="default-content">
                  <pre>{{ formatResultData(parsedResultData) }}</pre>
                </div>
              </div>
            </div>
          </div>
        </slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import {
  Loader,
  CircleCheckBig,
  ChevronsUpDown,
  ChevronsDownUp,
  FileText,
  FileEdit,
  FilePen,
  FolderSearch,
  Folder,
  Database,
  BookOpen,
  Globe,
  BarChart3,
  Image,
  Calculator,
  CheckSquare,
  Wrench,
  XCircle,
  HelpCircle,
  Terminal
} from 'lucide-vue-next'
import { useAgentStore } from '@/stores/agent'
import { storeToRefs } from 'pinia'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  },
  defaultExpanded: {
    type: Boolean,
    default: false
  },
  hideParams: {
    type: Boolean,
    default: false
  }
})

const agentStore = useAgentStore()
const { availableTools } = storeToRefs(agentStore)

const isExpanded = ref(props.defaultExpanded)

const toggleExpand = () => {
  isExpanded.value = !isExpanded.value
}

// Tool Name Logic
const toolName = computed(() => {
  const toolId = props.toolCall.name || props.toolCall.function?.name
  const toolsList = availableTools.value ? Object.values(availableTools.value) : []
  const tool = toolsList.find((t) => t.id === toolId)
  return tool ? tool.name : toolId
})

// Tool Icon Mapping
const toolIcon = computed(() => {
  const name = toolName.value.toLowerCase()
  // 文件操作
  if (name.includes('read_file') || name.includes('file')) return FileText
  if (name.includes('write_file')) return FileEdit
  if (name.includes('edit_file') || name.includes('replace')) return FilePen
  if (name.includes('glob') || name.includes('search_file')) return FolderSearch
  if (name.includes('list_directory') || name.includes('ls')) return Folder
  // 数据库
  if (name.includes('mysql')) return Database
  // 知识库/搜索
  if (name.includes('kb') || name.includes('knowledge')) return BookOpen
  if (name.includes('web_search') || name.includes('tavily')) return Globe
  // 图表/图像
  if (name.includes('chart')) return BarChart3
  if (name.includes('image') || name.includes('img')) return Image
  // 计算
  if (name.includes('calc') || name.includes('math')) return Calculator
  // 任务
  if (name.includes('task') || name.includes('todo')) return CheckSquare
  // 命令
  if (
    name.includes('execute') ||
    name.includes('cmd') ||
    name.includes('shell') ||
    name.includes('bash')
  )
    return Terminal
  // 向用户提问
  if (name.includes('ask_user_question') || name.includes('question')) return HelpCircle
  // 默认
  return Wrench
})

// Args Logic
const formattedArgs = computed(() => {
  const args = props.toolCall.args ? props.toolCall.args : props.toolCall.function?.arguments
  if (!args) return ''

  try {
    if (typeof args === 'string' && args.trim().startsWith('{')) {
      const parsed = JSON.parse(args)
      return JSON.stringify(parsed, null, 2)
    } else if (typeof args === 'object' && args !== null) {
      return JSON.stringify(args, null, 2)
    }
  } catch (e) {
    // ignore
  }
  return args
})

const hasParams = computed(() => {
  const argsStr = String(props.toolCall.args || props.toolCall.function?.arguments || '')
  return argsStr.length > 2
})

// Result Logic
const resultContent = computed(() => {
  return props.toolCall.tool_call_result?.content
})

const hasResult = computed(() => {
  return !!resultContent.value
})

// Default Result Rendering Logic
const parsedResultData = computed(() => {
  const content = resultContent.value
  if (typeof content === 'string') {
    try {
      return JSON.parse(content)
    } catch (error) {
      return content
    }
  }
  return content
})

const formatResultData = (data) => {
  if (typeof data === 'object') {
    return JSON.stringify(data, null, 2)
  }
  return String(data)
}

// Auto expand if loading
// Note: In the original code, expansion was managed by parent.
// Here we might want to default to expanded if it's loading?
// Original: :class="{ 'is-collapsed': !expandedToolCalls.has(toolCall.id) }"
// And expandedToolCalls defaults to empty set.
// User didn't specify default behavior, but usually we want to see what's happening.
// Let's keep it simple for now, defaulting to closed unless specified.
</script>

<style lang="less" scoped>
.tool-call-display {
  outline: 1px solid var(--gray-150);
  border-radius: 8px;
  overflow: hidden;
  transition: all 0.2s ease;
  margin-bottom: 10px;

  &:last-child {
    margin-bottom: 0;
  }

  .tool-header {
    padding: 8px 12px;
    font-size: 14px;
    font-weight: 500;
    color: var(--gray-800);
    border-bottom: 1px solid var(--gray-100);
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    user-select: none;
    position: relative;
    transition: color 0.2s ease;

    .anticon {
      color: var(--main-color);
      font-size: 16px;
    }

    .tool-name {
      font-weight: 600;
      color: var(--main-700);
    }

    span {
      display: flex;
      align-items: center;
      gap: 4px;
    }

    .tool-loader {
      margin-top: 2px;
      color: var(--main-700);
    }

    .tool-loader.rotate {
      animation: rotate 2s linear infinite;
    }

    .tool-loader.tool-success {
      color: var(--main-color);
    }

    .tool-loader.tool-error {
      color: var(--color-error-500);
    }

    .tool-loader.tool-loading {
      color: var(--color-info-500);
    }

    .tool-expand-icon {
      margin-left: auto;
      color: var(--gray-400);
      display: flex;
      align-items: center;
    }

    .tool-header-content {
      display: flex;
      align-items: center;
      flex: 1;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;

      :deep(.sep-header) {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 14px;
        width: 100%;
        overflow: hidden;
      }

      :deep(.keywords) {
        color: var(--main-700);
        font-weight: 600;
        font-size: 14px;
      }

      :deep(.note) {
        font-weight: 600;
        color: var(--main-700);
        white-space: nowrap;
        flex-shrink: 0;
      }

      :deep(span.code) {
        font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
      }

      :deep(.separator) {
        color: var(--gray-300);
        flex-shrink: 0;
      }

      :deep(.description) {
        color: var(--gray-700);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
      }

      :deep(.tag) {
        font-size: 12px;
        color: var(--gray-800);
        background-color: var(--gray-50);
        padding: 0px 4px;
        border-radius: 4px;
        margin-left: 8px;

        &.tag-yes {
          color: var(--main-500);
        }

        &.success {
          color: var(--color-success-500);
          background-color: var(--color-success-50);
        }
        &.error {
          color: var(--color-error-500);
          background-color: var(--color-error-50);
        }
      }
    }
  }

  .tool-content {
    transition: all 0.3s ease;

    .tool-params {
      padding: 8px 12px;
      background-color: var(--gray-25);
      border-bottom: 1px solid var(--gray-150);

      .tool-params-content {
        margin: 0;
        font-size: 12px;
        overflow-x: auto;
        color: var(--gray-700);
        line-height: 1.5;

        pre {
          margin: 0;
          font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
        }
      }
    }

    .tool-result {
      padding: 0;
      background-color: transparent;

      .tool-result-content {
        padding: 0;
        background-color: transparent;
      }
    }
  }

  &.is-collapsed {
    .tool-header {
      border-bottom: none;
    }
  }
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* Default Renderer Styles */
.tool-result-renderer {
  width: 100%;
  height: 100%;

  .default-result {
    background: var(--gray-0);
    border-radius: 8px;

    .default-content {
      background: var(--gray-0);
      padding: 12px;

      pre {
        margin: 0;
        font-size: 12px;
        line-height: 1.4;
        color: var(--gray-700);
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 300px;
        overflow-y: auto;
        background: var(--gray-50);
        padding: 10px;
        border-radius: 4px;
      }
    }
  }
}
</style>
