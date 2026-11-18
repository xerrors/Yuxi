<template>
  <div v-if="displayEntries.length > 0" class="tool-calls-container">
    <div v-if="shouldCollapseToolCalls" class="tool-calls-heading" :class="{ 'is-active': isActive }">
      <!-- 复用最新条目的原始 header；交互由同级展开按钮统一承接。 -->
      <div v-if="isActive" class="live-header" inert aria-hidden="true">
        <ReasoningBlockComponent
          v-if="latestEntry.type === 'reasoning'"
          :content="latestEntry.content"
          :is-active="false"
        />
        <ToolCallRenderer
          v-else
          :key="latestEntry.key"
          :tool-call="latestEntry.toolCall"
          :cooperation-view="cooperationView"
          appearance="timeline"
        />
      </div>
      <button
        type="button"
        class="tool-calls-summary"
        :class="{ 'is-expanded': areToolCallsExpanded }"
        :aria-expanded="areToolCallsExpanded"
        :aria-label="isActive ? latestEntryLabel : undefined"
        @click="toggleToolCallsExpanded"
      >
        <span v-if="!isActive" class="summary-leading">
          <Atom size="15" aria-hidden="true" />
        </span>
        <span v-if="!isActive" class="summary-content">
          <span class="summary-title">{{ toolCallsSummaryTitle }}</span>
          <span class="summary-separator" v-if="normalizedToolCalls.length > 1 && toolCallsNamesMeta"
            >·</span
          >
          <span class="summary-meta" v-if="normalizedToolCalls.length > 1 && toolCallsNamesMeta">{{
            toolCallsNamesMeta
          }}</span>
          <span class="summary-status-tag" v-if="statusSummary">{{ statusSummary }}</span>
        </span>
        <span class="summary-trailing">
          <ChevronDown
            :size="14"
            class="summary-chevron"
            :class="{ 'is-collapsed': !areToolCallsExpanded }"
            aria-hidden="true"
          />
        </span>
      </button>
    </div>

    <div
      class="tool-calls-collapse-panel"
      :class="{ 'is-expanded': !shouldCollapseToolCalls || areToolCallsExpanded }"
    >
      <div class="tool-calls-collapse-inner">
        <div class="tool-calls-panel" :class="{ 'is-grouped': shouldCollapseToolCalls }">
          <div v-for="entry in groupedEntries" :key="entry.key" class="tool-call-container">
            <ReasoningBlockComponent
              v-if="entry.type === 'reasoning'"
              :content="entry.content"
              :is-active="isActive && entry === displayEntries[displayEntries.length - 1]"
            />
            <CooperationTool
              v-else-if="entry.type === 'cooperation'"
              :tool-call="entry.toolCalls[0]"
              :tool-calls="entry.toolCalls"
              :cooperation-view="cooperationView"
            />
            <ToolCallRenderer
              v-else
              :tool-call="entry.toolCall"
              :cooperation-view="cooperationView"
              appearance="timeline"
              :default-expanded="false"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ChevronDown, Atom } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { useAgentStore } from '@/modules/agents'
import ReasoningBlockComponent from '@/modules/session/ui/ReasoningBlockComponent.vue'
import { groupCooperationEntries } from '@/modules/session/model/cooperationToolView'
import { ToolCallRenderer } from '@/modules/session/ui/tools/index.js'
import {
  getToolCallId,
  getToolCallDisplayStatus,
  getToolName,
  findToolInList,
  normalizeToolCalls
} from '@/modules/session/ui/tools/toolRegistry'

import { CooperationTool } from './tools/toolRenderers.js'

const agentStore = useAgentStore()
const { availableTools, toolMetadata } = storeToRefs(agentStore)


const props = defineProps({
  cooperationView: { type: Object, default: null },
  toolCalls: {
    type: Array,
    default: () => []
  },
  entries: {
    type: Array,
    default: () => []
  },
  isActive: {
    type: Boolean,
    default: false
  }
})

const normalizedToolCalls = computed(() => normalizeToolCalls(props.toolCalls))

const displayEntries = computed(() =>
  props.entries.length
    ? props.entries
    : normalizedToolCalls.value.map((toolCall, index) => ({
        type: 'tool',
        key: toolCall.id || `${getToolCallId(toolCall)}-${index}`,
        toolCall
      }))
)
const groupedEntries = computed(() => props.cooperationView ? groupCooperationEntries(displayEntries.value) : displayEntries.value)
const hasReasoning = computed(() =>
  displayEntries.value.some((entry) => entry.type === 'reasoning')
)
const shouldCollapseToolCalls = computed(() => displayEntries.value.length > 1 || hasReasoning.value)
const areToolCallsExpanded = ref(false)

watch(() => props.isActive, () => {
  areToolCallsExpanded.value = false
})

const latestEntry = computed(() => displayEntries.value.at(-1))
const latestEntryLabel = computed(() =>
  latestEntry.value?.type === 'reasoning'
    ? '思考'
    : getToolCallLabel(latestEntry.value?.toolCall)
)

// 工具名称展示优先级：display_label > 完整工具元数据中的 display name > 前端兜底名称映射 > 工具 id
const getToolCallLabel = (toolCall) => {
  const displayLabel = String(toolCall?.display_label || '').trim()
  if (displayLabel) return displayLabel

  const toolId = getToolCallId(toolCall)
  const toolsList = toolMetadata.value.length
    ? toolMetadata.value
    : availableTools.value
      ? Object.values(availableTools.value)
      : []
  const tool = findToolInList(toolId, toolsList)
  return tool?.name && tool.name !== toolId ? tool.name : getToolName(toolId)
}

const toolCallsSummaryTitle = computed(() => {
  if (normalizedToolCalls.value.length === 0) return props.isActive ? 'Thinking...' : '推理过程'
  if (hasReasoning.value) return `推理与工具调用 · ${normalizedToolCalls.value.length} 个工具`
  if (normalizedToolCalls.value.length === 1) {
    return `调用: ${getToolCallLabel(normalizedToolCalls.value[0])}`
  }
  return `已调用 ${normalizedToolCalls.value.length} 个工具`
})

const toolCallsNamesMeta = computed(() => {
  const names = normalizedToolCalls.value.map(getToolCallLabel).filter(Boolean)
  const uniqueNames = [...new Set(names)]
  const visibleNames = uniqueNames.slice(0, 3)

  if (visibleNames.length === 0) return ''

  return `${visibleNames.join(' · ')}${uniqueNames.length > visibleNames.length ? ` +${uniqueNames.length - visibleNames.length}` : ''}`
})

const statusSummary = computed(() => {
  const states = normalizedToolCalls.value.map((toolCall) =>
    getToolCallDisplayStatus(toolCall)
  )
  const runningCount = states.filter((state) => state === 'running').length
  const errorCount = states.filter((state) => state === 'error').length
  const incompleteCount = states.filter((state) => state === 'incomplete').length

  const parts = []
  if (errorCount > 0) parts.push(`${errorCount} 失败`)
  if (incompleteCount > 0) parts.push(`${incompleteCount} 未完成`)
  if (runningCount > 0) parts.push(`${runningCount} 进行中`)

  return parts.join(' · ')
})

const toggleToolCallsExpanded = () => {
  areToolCallsExpanded.value = !areToolCallsExpanded.value
}
</script>

<style lang="less" scoped>
.tool-calls-container {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  margin-block: 8px;
  padding: 0;
  display: flex;
  flex-direction: column;

  .tool-calls-heading {
    position: relative;
    min-width: 0;

    &.is-active {
      width: fit-content;
      max-width: 100%;
      padding-right: 22px;

      .tool-calls-summary {
        position: absolute;
        inset: 0;
        justify-content: flex-end;
      }
    }
  }

  .live-header {
    min-width: 0;
    pointer-events: none;
    // 遮罩只改变明暗，保留专用 header 的继承色与文字布局。
    mask-image: linear-gradient(110deg, #000 35%, #0005 50%, #000 65%);
    mask-size: 250% 100%;
    animation: live-header-shimmer 2s linear infinite;

    :deep(.tool-expand-icon),
    :deep(.tool-content) {
      display: none;
    }

    :deep(.tool-loader.rotate) {
      animation: none;
    }
  }

  .tool-calls-summary {
    appearance: none;
    width: 100%;
    max-width: 100%;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 28px;
    color: var(--gray-600);
    text-align: left;
    cursor: pointer;
    outline: none;
    border: none;
    padding: 0;
    transition: color 0.15s ease;
    user-select: none;
    background: transparent;

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 2px;
    }

    &:hover,
    &.is-expanded {
      color: var(--gray-800);
    }

    &:is(:hover, :focus-visible, .is-expanded) .summary-trailing {
      opacity: 1;
    }

    .summary-leading {
      display: inline-flex;
      align-items: center;
      flex-shrink: 0;
    }

    .summary-content {
      min-width: 0;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      font-size: 13px;
    }

    .summary-title {
      font-weight: 400;
      white-space: nowrap;
    }

    .summary-separator {
      flex-shrink: 0;
    }

    .summary-meta {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .summary-status-tag {
      margin-left: 4px;
      font-size: 11px;
      padding: 0 4px;
      border-radius: 4px;
      white-space: nowrap;
      font-weight: normal;
    }

    .summary-trailing {
      display: inline-flex;
      align-items: center;
      flex-shrink: 0;
      opacity: 0;
    }

    .summary-chevron {
      flex-shrink: 0;
      transition: transform 0.22s cubic-bezier(0.16, 1, 0.3, 1);

      &.is-collapsed {
        transform: rotate(-90deg);
      }
    }
  }

  .tool-calls-collapse-panel {
    display: grid;
    grid-template-rows: 0fr;
    transition:
      grid-template-rows 0.24s cubic-bezier(0.16, 1, 0.3, 1),
      visibility 0.24s ease;
    visibility: hidden;
    min-width: 0;

    &.is-expanded {
      grid-template-rows: 1fr;
      visibility: visible;
    }
  }

  .tool-calls-collapse-inner {
    min-width: 0;
    overflow: hidden;
    min-height: 0;
    opacity: 0;
    transform: translateY(-4px);
    transition:
      opacity 0.2s ease,
      transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .tool-calls-collapse-panel.is-expanded .tool-calls-collapse-inner {
    opacity: 1;
    transform: translateY(0);
  }

  .tool-calls-panel {
    margin-top: 6px;
    margin-bottom: 8px;

    &.is-grouped {
      margin-left: 7px;
      padding-left: 16px;
      border-left: 1px solid var(--gray-150);
    }
  }

  .tool-call-container {
    min-width: 0;
    margin-bottom: 4px;
    &:last-child {
      margin-bottom: 0;
    }
  }
}

@keyframes live-header-shimmer {
  from { mask-position: 150% 0; }
  to { mask-position: -50% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .tool-calls-container {
    .live-header {
      animation: none;
      mask-image: none;
    }

    .tool-calls-summary .summary-chevron,
    .tool-calls-collapse-panel,
    .tool-calls-collapse-inner {
      transition: none;
    }
  }
}
</style>
