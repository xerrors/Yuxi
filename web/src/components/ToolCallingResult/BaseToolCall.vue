<template>
  <div
    class="tool-call-display"
    :class="{ 'is-collapsed': !isExpanded, 'is-timeline': isTimeline }"
  >
    <!-- Header Slot -->
    <div class="tool-header" @click="toggleExpand">
      <!-- Fixed Status Icon -->
      <span v-if="effectiveStatus === 'completed'">
        <component v-if="toolIcon" :is="toolIcon" size="16" class="tool-loader tool-success" />
        <CheckCircle v-else size="16" class="tool-loader tool-success" />
      </span>
      <span v-else-if="effectiveStatus === 'error'">
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
      <div class="tool-result" style="opacity: 0.8" v-if="hasResult || forceShowResult">
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
import { Loader, ChevronsUpDown, ChevronsDownUp, XCircle, CheckCircle } from 'lucide-vue-next'
import { useAgentStore } from '@/stores/agent'
import { storeToRefs } from 'pinia'
import { getToolCallId, getToolIcon } from './toolRegistry'

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
  },
  appearance: {
    type: String,
    default: 'card'
  },
  // 外部可覆盖状态以驱动图标：'running' | 'completed' | 'failed'（用于结果不随流式返回的工具，如 task）
  status: {
    type: String,
    default: ''
  },
  // 即使没有 tool_call_result 也展示结果区（配合外部提供的结果内容，如 task 的 result_preview）
  forceShowResult: {
    type: Boolean,
    default: false
  }
})

const agentStore = useAgentStore()
const { availableTools } = storeToRefs(agentStore)

const isExpanded = ref(props.defaultExpanded)
const isTimeline = computed(() => props.appearance === 'timeline')

const toggleExpand = () => {
  isExpanded.value = !isExpanded.value
}

// 图标状态：优先用外部传入的 status，否则按 tool_call_result/status 推断
const effectiveStatus = computed(() => {
  if (props.status) return props.status
  if (props.toolCall.status === 'success' || props.toolCall.tool_call_result) return 'completed'
  if (props.toolCall.status === 'error') return 'failed'
  return 'running'
})

// Tool Name Logic
const toolId = computed(() => getToolCallId(props.toolCall))

const toolName = computed(() => {
  const toolsList = availableTools.value ? Object.values(availableTools.value) : []
  const tool = toolsList.find((t) => t.id === toolId.value)
  return tool ? tool.name : toolId.value
})

// Tool Icon Mapping
const toolIcon = computed(() => getToolIcon(toolId.value))

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
  } catch {
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
    } catch {
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
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  overflow: hidden;
  transition: all 0.2s ease;
  margin-bottom: 8px;

  &:last-child {
    margin-bottom: 0;
  }

  .tool-header {
    padding: 6px 10px;
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-800);
    border-bottom: 1px solid var(--gray-50);
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    user-select: none;
    position: relative;
    transition: background-color 0.2s ease;

    &:hover {
      background-color: var(--gray-25);
    }

    & > span {
      display: flex;
      align-items: center;
    }

    .tool-name {
      font-weight: 600;
      color: var(--main-700);
    }

    .tool-loader {
      margin-top: 0;
      color: var(--main-600);
    }

    .tool-loader.rotate {
      animation: rotate 2s linear infinite;
    }

    .tool-loader.tool-success {
      color: var(--color-success-500);
    }

    .tool-loader.tool-error {
      color: var(--color-error-500);
    }

    .tool-loader.tool-loading {
      color: var(--color-info-500);
    }

    .tool-expand-icon {
      margin-left: auto;
      color: var(--gray-300);
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
      color: var(--gray-600);

      :deep(.sep-header) {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        width: 100%;
        overflow: hidden;

        .note {
          font-weight: 500;
          color: var(--gray-600);
          flex-shrink: 0;
        }

        .separator {
          color: var(--gray-300);
          flex-shrink: 0;
        }

        .description {
          color: var(--gray-600);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          min-width: 0;
        }
      }

      :deep(.keywords) {
        color: var(--main-700);
        font-weight: 600;
      }

      :deep(.tag) {
        font-size: 11px;
        color: var(--gray-500);
        // background-color: var(--gray-50);
        padding: 0px 4px;
        border-radius: 4px;
        margin-left: 8px;
        white-space: nowrap;

        &.success {
          color: var(--color-success-500);
          // background-color: var(--color-success-50);
        }
        &.error {
          color: var(--color-error-500);
          // background-color: var(--color-error-50);
        }
      }
    }
  }

  .tool-content {
    transition: all 0.3s ease;

    .tool-params {
      padding: 8px 12px;
      background-color: var(--gray-25);
      border-bottom: 1px solid var(--gray-50);

      .tool-params-content {
        margin: 0;
        font-size: 12px;
        overflow-x: auto;
        color: var(--gray-600);
        line-height: 1.5;

        pre {
          margin: 0;
          font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
        }
      }
    }
  }

  &.is-collapsed {
    .tool-header {
      border-bottom: none;
    }
  }

  &.is-timeline {
    border: none;
    border-radius: 0;
    overflow: visible;
    margin-bottom: 0;
    padding-left: 0;
    position: relative;

    .tool-header {
      padding: 4px 0;
      background-color: transparent;
      border-bottom: none;
      color: var(--gray-500);
      gap: 10px;

      &:hover {
        background-color: transparent;
        color: var(--gray-700);
      }

      .tool-name {
        color: var(--gray-600);
      }

      .tool-loader {
        color: var(--gray-600);
      }

      .tool-expand-icon {
        opacity: 0.5;
      }

      .tool-header-content {
        font-size: 13px;
        color: var(--gray-500);
      }
    }

    .tool-content {
      margin: 4px 0 8px 8px;
      padding-left: 8px;
      border-left: 1px solid var(--gray-100);

      &:hover {
        border-left-color: var(--gray-400);
      }

      .tool-params {
        padding: 4px 0 8px;
        background-color: transparent;
        border-bottom: none;
      }
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
      padding: 8px 0px;

      pre {
        margin: 0;
        font-size: 12px;
        line-height: 1.4;
        color: var(--gray-700);
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 300px;
        overflow-y: auto;
        background: var(--gray-25);
        padding: 10px;
        border-radius: 4px;
      }
    }
  }
}
</style>
