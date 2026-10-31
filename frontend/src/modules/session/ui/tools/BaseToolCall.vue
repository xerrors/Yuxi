<template>
  <div
    class="tool-call-display"
    :class="{ 'is-collapsed': summaryOnly || !isExpanded, 'is-timeline': isTimeline, 'is-summary': summaryOnly }"
  >
    <!-- Header Slot -->
    <div
      class="tool-header"
      :role="summaryOnly ? undefined : 'button'"
      :tabindex="summaryOnly ? undefined : 0"
      :aria-expanded="summaryOnly || headerAction ? undefined : isExpanded"
      :title="!summaryOnly && headerAction ? '打开详情面板' : undefined"
      @click="toggleExpand"
      @keydown.enter.self="toggleExpand"
      @keydown.space.self.prevent="toggleExpand"
    >
      <!-- Fixed Status Icon -->
      <slot name="icon" :status="effectiveStatus">
        <span v-if="effectiveStatus === 'completed'">
          <component v-if="toolIcon" :is="toolIcon" size="15" class="tool-loader tool-success" />
          <CheckCircle v-else size="15" class="tool-loader tool-success" />
        </span>
        <span v-else-if="effectiveStatus === 'waiting'">
          <Clock size="15" class="tool-loader tool-loading" />
        </span>
        <span v-else-if="['error', 'incomplete'].includes(effectiveStatus)">
          <XCircle size="15" class="tool-loader tool-error" />
        </span>
        <span v-else>
          <Loader size="15" class="tool-loader rotate tool-loading" />
        </span>
      </slot>

      <!-- Content Area with Slots -->
      <div class="tool-header-content">
        <!-- Generic Header Slot (Overrides specific slots if provided) -->
        <template v-if="$slots.header">
          <slot name="header" :tool-call="toolCall" :tool-name="toolName"></slot>
          <span v-if="summaryOnly && effectiveStatus === 'error'" class="tool-incomplete-label">执行失败</span>
          <span v-else-if="effectiveStatus === 'incomplete'" class="tool-incomplete-label">已中断</span>
          <span v-else-if="effectiveStatus === 'waiting'" class="tool-incomplete-label"
            >等待协作</span
          >
        </template>

        <!-- Specific State Slots (Fallback) -->
        <template v-else>
          <slot
            name="header-success"
            v-if="effectiveStatus === 'completed'"
            :tool-name="toolName"
            :result-content="resultContent"
          >
            工具&nbsp; <span class="tool-name">{{ toolName }}</span> &nbsp; 执行完成
          </slot>

          <slot
            name="header-error"
            v-else-if="effectiveStatus === 'error'"
            :tool-name="toolName"
            :error-message="toolCall.error_message"
          >
            工具&nbsp; <span class="tool-name">{{ toolName }}</span> &nbsp; 执行失败
            <span v-if="toolCall.error_message">（{{ toolCall.error_message }}）</span>
          </slot>

          <span v-else-if="effectiveStatus === 'waiting'">
            工具&nbsp; <span class="tool-name">{{ toolName }}</span> &nbsp; 等待协作
          </span>
          <span v-else-if="effectiveStatus === 'incomplete'">
            工具&nbsp; <span class="tool-name">{{ toolName }}</span> &nbsp; 已中断
          </span>

          <slot name="header-running" v-else :tool-name="toolName">
            正在调用工具: &nbsp; <span class="tool-name">{{ toolName }}</span>
          </slot>
        </template>
      </div>

      <!-- Fixed Expand Icon -->
      <span v-if="!summaryOnly" class="tool-expand-icon">
        <ChevronRight v-if="headerAction" size="14" />
        <ChevronsDownUp v-else-if="isExpanded" size="14" />
        <ChevronsUpDown v-else size="14" />
      </span>
    </div>

    <!-- Content Area -->
    <CollapseTransition>
      <div v-if="!summaryOnly && !headerAction && isExpanded" class="tool-content">
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
        <div class="tool-result" v-if="hasResult || forceShowResult || hasToolError">
          <div v-if="hasToolError" class="tool-error-result">
            <ToolResultContent
              :content="hasResult ? resultContent : toolCall.error_message || '工具执行失败'"
            />
          </div>
          <slot v-else name="result" :tool-call="toolCall" :result-content="resultContent">
            <div class="tool-result-content" :data-tool-call-id="toolCall.id">
              <!-- Default rendering -->
              <div class="tool-result-renderer">
                <div class="default-result">
                  <div class="default-content">
                    <ToolResultContent :content="resultContent" />
                  </div>
                </div>
              </div>
            </div>
          </slot>
        </div>
      </div>
    </CollapseTransition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import {
  ChevronRight,
  Clock,
  Loader,
  ChevronsUpDown,
  ChevronsDownUp,
  XCircle,
  CheckCircle
} from '@lucide/vue'
import { useAgentStore } from '@/modules/agents'
import { storeToRefs } from 'pinia'
import CollapseTransition from '@/shared/ui/CollapseTransition.vue'
import ToolResultContent from './ToolResultContent.vue'
import {
  getToolCallId,
  getToolIcon,
  getToolName,
  findToolInList,
  getToolCallStatus
} from './toolRegistry'
import { getToolCallResultContent } from '@/modules/session/model/toolCallProjection'

const props = defineProps({
  headerAction: { type: Function, default: null },
  // 仅展示动作摘要的工具没有展开入口，完整回执由消息调试面板承载。
  summaryOnly: { type: Boolean, default: false },
  toolCall: {
    type: Object,
    default: () => ({})
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
  // 特殊工具可覆盖运行态；调用或结果中的明确错误始终优先。
  status: {
    type: String,
    default: ''
  },
  // 即使没有 tool_call_result 也展示结果区（配合外部提供的结果内容）
  forceShowResult: {
    type: Boolean,
    default: false
  }
})

const agentStore = useAgentStore()
const { availableTools, toolMetadata } = storeToRefs(agentStore)

const isExpanded = ref(props.defaultExpanded)
const isTimeline = computed(() => props.appearance === 'timeline')

const toggleExpand = () => {
  if (props.summaryOnly) return
  if (props.headerAction) {
    props.headerAction()
    return
  }
  isExpanded.value = !isExpanded.value
}

const toolStatus = computed(() => getToolCallStatus(props.toolCall))
const hasToolError = computed(() => toolStatus.value === 'error')
const effectiveStatus = computed(() => {
  if (hasToolError.value || props.status === 'failed') return 'error'
  if (toolStatus.value === 'waiting') return 'waiting'
  if (toolStatus.value === 'incomplete') return 'incomplete'
  return props.status || toolStatus.value
})

// Tool Name Logic
// 展示优先级：完整工具元数据中的 display name > 前端兜底名称映射 > 工具 id
const toolId = computed(() => getToolCallId(props.toolCall))

const toolName = computed(() => {
  const tool = findToolInList(toolId.value, toolMetadataList.value)
  return tool ? tool.name : getToolName(toolId.value)
})

const toolMetadataList = computed(() =>
  toolMetadata.value.length
    ? toolMetadata.value
    : availableTools.value
      ? Object.values(availableTools.value)
      : []
)

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
const resultContent = computed(() => getToolCallResultContent(props.toolCall))

const hasResult = computed(() => {
  return resultContent.value != null && resultContent.value !== ''
})
</script>

<style lang="less" scoped>
.tool-call-display {
  min-width: 0;
  max-width: 100%;
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

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 2px;
    }

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
      flex-shrink: 0;
      margin-left: auto;
      color: var(--gray-300);
      display: flex;
      align-items: center;
    }

    .tool-header-content {
      min-width: 0;
      display: flex;
      align-items: center;
      flex: 1;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      color: var(--gray-600);

      .tool-incomplete-label {
        flex-shrink: 0;
        margin-left: 8px;
      }

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

  &.is-summary .tool-header {
    cursor: default;
    &:hover { background-color: transparent; }
    .tool-header-content :deep(.sep-header) { width: auto; }
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

        &.tool-error {
          color: var(--color-error-500);
        }
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

.tool-error-result {
  --tool-result-text-color: var(--color-error-700);
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
    }
  }
}
</style>
