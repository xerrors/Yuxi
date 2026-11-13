<template>
  <BaseToolCall
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
    :incomplete-label="receipt?.status === 'outcome_unknown' ? '执行结果未知' : '已中断'"
  >
    <template v-if="status !== 'running' && status !== 'waiting'" #icon>
      <span class="abridge-tool-icon">
        <component :is="actionIcon" :size="15" class="tool-loader" aria-hidden="true" />
      </span>
    </template>
    <template #header>
      <div class="sep-header">
        <span class="note">{{ actionLabel }}</span>
        <span v-if="target" class="separator">|</span>
        <span v-if="target" class="description" :title="target">{{ target }}</span>
        <span v-if="status === 'error'" class="tag error">执行失败</span>
      </div>
    </template>
    <template #result>
      <ToolResultContent v-if="commandOutput" :content="commandOutput" />
      <ToolResultContent v-if="images.length" :content="images" />
      <details v-if="rawResult">
        <summary>原始结果</summary>
        <ToolResultContent :content="rawResult" />
      </details>
    </template>
    <template v-if="toolId === 'command_execute' && result" #error-result>
      <ToolResultContent v-if="commandOutput" :content="commandOutput" />
      <details v-if="rawResult">
        <summary>原始结果</summary>
        <ToolResultContent :content="rawResult" />
      </details>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import { Keyboard, MousePointerClick } from '@lucide/vue'
import BaseToolCall from '../BaseToolCall.vue'
import ToolResultContent from '../ToolResultContent.vue'
import {
  getToolCallId, getToolCallResultContent, getToolCallStatus,
  getToolResultParts, parseToolCallArgs
} from '../../../model/toolCallProjection.js'
import { ABRIDGE_TOOL_NAMES, parseAbridgeResult } from '../../../model/abridgeToolResult.js'
import { getToolIcon } from '../../../model/toolIcons.js'

const props = defineProps({
  toolCall: { type: Object, required: true },
  appearance: { type: String, default: 'card' },
  defaultExpanded: { type: Boolean, default: false }
})
const toolId = computed(() => getToolCallId(props.toolCall))
const args = computed(() => parseToolCallArgs(props.toolCall))
const content = computed(() => getToolCallResultContent(props.toolCall))
const receipt = computed(() => parseAbridgeResult(content.value))
const result = computed(() => receipt.value?.result)
/** 将设备命令的输出投影为现有命令工具使用的纯文本结果。 */
const commandOutput = computed(() => {
  if (toolId.value !== 'command_execute' || !result.value) return ''
  const output = result.value
  return [
    output.stdout,
    output.stderr && `标准错误：\n${output.stderr}`,
    (output.truncated || receipt.value.output_truncated || receipt.value.capture_truncated) && '输出已截断',
    output.timed_out && '命令执行超时'
  ].filter(Boolean).join('\n')
})
const status = computed(() => getToolCallStatus(props.toolCall))
const actionIcon = computed(() => {
  if (['computer_act', 'browser_act'].includes(toolId.value)) {
    const type = args.value.action?.type
    if (['type', 'key', 'fill'].includes(type)) return Keyboard
    if (['click', 'double_click'].includes(type)) return MousePointerClick
  }
  return getToolIcon(toolId.value)
})
const isBrowserTool = computed(() => ['browser_open', 'browser_observe', 'browser_act'].includes(toolId.value))
const isBrowserSession = computed(() =>
  (receipt.value?.capabilities || args.value.capabilities || []).includes('browser'))
const observation = computed(() => result.value?.observation ||
  (['computer_observe', 'browser_observe'].includes(toolId.value) ? result.value : null))
const pageTitle = computed(() => observation.value?.title || result.value?.title || '')
const pageUrl = computed(() => observation.value?.url || result.value?.url || '')
const actionNames = { click: '点击', double_click: '双击', type: '输入文本', key: '按键', scroll: '滚动', drag: '拖动' }
const actionLabel = computed(() => {
  if (toolId.value === 'session_open' && isBrowserSession.value) return '连接浏览器'
  if (toolId.value === 'browser_act') {
    return { click: '点击网页元素', fill: '填写网页内容' }[args.value.action?.type] || ABRIDGE_TOOL_NAMES.browser_act
  }
  return toolId.value === 'computer_act'
    ? actionNames[args.value.action?.type] || ABRIDGE_TOOL_NAMES.computer_act
    : ABRIDGE_TOOL_NAMES[toolId.value]
})
const target = computed(() => {
  const input = args.value
  if (isBrowserTool.value) return pageTitle.value || pageUrl.value || input.url || input.action?.element_ref || input.query || ''
  if (toolId.value === 'command_execute') return input.command || ''
  if (toolId.value === 'computer_act') {
    const action = input.action || {}
    if (action.type === 'key') return action.key || ''
    if (action.x != null && action.y != null) return `(${action.x}, ${action.y})`
    return action.element_ref || ''
  }
  if (toolId.value === 'devices_list' && receipt.value?.devices) return `${receipt.value.devices.length} 台设备`
  return observation.value?.app_name || input.query || input.bundle_id || input.device_id || ''
})
const resultParts = computed(() => getToolResultParts(content.value))
const rawResult = computed(() => resultParts.value.filter((part) => !part.image).map((part) => part.text).join('\n'))
const images = computed(() => resultParts.value
  .filter((part) => part.image)
  .map((part) => ({ type: 'image_url', image_url: { url: part.src } })))
</script>

<style lang="less" scoped>
.abridge-tool-icon {
  display: inline-flex;
  flex: 0 0 15px;
  align-items: center;
  justify-content: center;
  line-height: 0;
}
details { min-width: 0; margin-top: 8px; }
summary {
  cursor: pointer;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 20px;
  margin-bottom: 6px;
}
</style>
