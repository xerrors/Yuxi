<template>
  <BaseToolCall
    :tool-call="toolCall"
    :icon="actionIcon"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
    :command="command"
    :hide-params="Boolean(deviceDetail)"
    :error-label="receipt?.status === 'outcome_unknown' ? '执行结果未知' : operationStopLabel || '执行失败'"
    :incomplete-label="receipt?.status === 'outcome_unknown' ? '执行结果未知' : '已中断'"
  >
    <template #header>
      <div class="sep-header">
        <span class="note">{{ actionLabel }}</span>
        <span v-if="target" class="separator">|</span>
        <span v-if="headerDevices.length" class="description device-summary" :title="target">
          <span v-if="toolId === 'devices_list'" class="device-count">{{ headerDevices.length }} 台设备</span>
          <span v-for="device in headerDevices" :key="device.id" class="device-item">
            <img v-if="device.icon" :src="device.icon" :alt="device.platformLabel" />
            <Monitor v-else :size="14" aria-label="设备" />
            <span class="device-name">{{ device.name || '未命名设备' }}</span>
          </span>
          <span v-if="sessionCapabilityText" class="session-capabilities">{{ sessionCapabilityText }}</span>
        </span>
        <span v-else-if="target" class="description" :title="target">{{ target }}</span>
      </div>
    </template>
    <template #result>
      <ToolResultContent v-if="display.operationError" :content="display.operationError" />
      <dl v-if="deviceDetail" class="device-detail">
        <dt>设备状态</dt><dd>{{ deviceStatusText }}</dd>
        <dt>平台</dt><dd>{{ deviceDetail.platformLabel }}</dd>
        <template v-if="deviceDetail.capabilities?.length">
          <dt>授权能力</dt><dd>{{ deviceCapabilityText }}</dd>
        </template>
      </dl>
      <template v-if="display.browserReadResult">
        <p class="device-file-summary">{{ display.browserReadSummary }}</p>
        <template v-if="display.browserReadResult.mode === 'tables'">
          <p v-if="!display.browserReadResult.rows.length" class="device-file-summary">未读取到表格行</p>
          <div v-for="(rows, table) in display.browserTables" :key="table" class="browser-table">
            <table>
              <caption>表格 {{ Number(table) + 1 }}（当前页）</caption>
              <tbody>
                <tr v-for="row in rows" :key="row.row">
                  <td v-for="(cell, index) in row.cells" :key="index" :rowspan="cell.row_span" :colspan="cell.col_span">{{ cell.text }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <pre v-else-if="display.browserReadResult.text" class="device-file-content">{{ display.browserReadResult.text }}</pre>
        <p v-else class="device-file-summary">未读取到网页文本</p>
        <p v-if="display.browserReadResult.next_offset != null" class="device-file-summary">还有后续内容</p>
        <p v-if="display.browserReadResult.truncated" class="device-file-summary">快照已截断</p>
      </template>
      <p v-if="display.browserWaitMatched" class="device-file-summary">等待条件已满足</p>
      <ToolResultContent v-if="display.browserScriptSummary" :content="display.browserScriptSummary" />
      <pre v-if="display.browserScriptValue !== undefined" class="device-file-content" aria-label="浏览器脚本返回值">{{ display.browserScriptValue }}</pre>
      <p v-if="display.browserScriptValue !== undefined && display.browserScriptResult.value_truncated" class="device-file-summary">返回值已截断</p>
      <template v-if="display.fileResult">
        <template v-if="toolId === 'files_list'">
          <p v-if="!display.fileResult.entries.length" class="device-file-summary">目录为空</p>
          <ul v-else class="device-file-list" aria-label="设备目录内容">
            <li v-for="entry in display.fileResult.entries" :key="entry.name">
              <span class="device-file-name" :title="entry.name + (entry.is_symlink ? '（符号链接）' : '')">{{ entry.name }}{{ entry.is_symlink ? '*' : '' }}</span>
            </li>
          </ul>
          <p v-if="display.fileResult.truncated" class="device-file-summary">目录列表已截断</p>
        </template>
        <template v-else-if="toolId === 'file_read'">
          <pre v-if="display.fileResult.content" class="device-file-content">{{ display.fileResult.content }}</pre>
          <p v-else class="device-file-summary">文件为空</p>
          <p v-if="display.fileResult.truncated" class="device-file-summary">内容已截断</p>
        </template>
        <p v-else class="device-file-summary">
          写入成功<span v-if="display.fileResult.size != null"> · {{ formatFileSize(display.fileResult.size) }}</span><span v-if="display.fileResult.overwritten"> · 已覆盖</span>
        </p>
      </template>
      <ToolResultContent v-if="display.commandOutput" :content="display.commandOutput" />
      <ToolResultContent v-if="display.images.length" :content="display.images" />
      <details v-if="display.rawResult">
        <summary>原始结果</summary>
        <ToolResultContent :content="display.rawResult" />
      </details>
    </template>
    <template v-if="(toolId === 'operation_status' && receipt) || display.commandResult || display.browserScriptResult" #error-result>
      <ToolResultContent v-if="display.operationError" :content="display.operationError" />
      <ToolResultContent v-if="display.browserScriptSummary" :content="display.browserScriptSummary" />
      <ToolResultContent v-if="display.commandOutput" :content="display.commandOutput" />
      <details v-if="display.rawResult">
        <summary>原始结果</summary>
        <ToolResultContent :content="display.rawResult" />
      </details>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import { Keyboard, Monitor, MousePointerClick } from '@lucide/vue'
import { deviceAppearance } from '@/shared/lib/deviceAppearance'
import { formatFileSize } from '@/shared/lib/file_utils'
import BaseToolCall from '../BaseToolCall.vue'
import ToolResultContent from '../ToolResultContent.vue'
import {
  getToolCallId, getToolCallStatus, getToolCallResultContent, getToolResultParts,
  parseToolCallArgs
} from '../../../model/toolCallProjection.js'
import { ABRIDGE_TOOL_NAMES, projectAbridgeResult } from '../../../model/abridgeToolResult.js'

const props = defineProps({
  toolCall: { type: Object, required: true },
  appearance: { type: String, default: 'card' },
  defaultExpanded: { type: Boolean, default: false }
})
const toolId = computed(() => getToolCallId(props.toolCall))
const args = computed(() => parseToolCallArgs(props.toolCall))
const command = computed(() => toolId.value === 'command_execute' && typeof args.value?.command === 'string' ? args.value.command : '')
const status = computed(() => getToolCallStatus(props.toolCall))
const display = computed(() => {
  const content = getToolCallResultContent(props.toolCall)
  return projectAbridgeResult({ toolId: toolId.value, content, status: status.value, parts: getToolResultParts(content) })
})
const receipt = computed(() => display.value.receipt)
const result = computed(() => receipt.value?.result)
// device_get 直接返回设备对象；只有完成的查询才以该回执替代参数摘要。
const deviceDetail = computed(() =>
  toolId.value === 'device_get' && status.value === 'completed' && receipt.value?.id
    ? { ...receipt.value, ...deviceAppearance(receipt.value) }
    : null)
const deviceStatusText = computed(() => {
  const device = deviceDetail.value
  if (device?.status === 'revoked') return '已撤销'
  if (device?.paused) return '已暂停'
  return { online: '在线', offline: '离线' }[device?.status] || device?.status || '未知'
})
const capabilityNames = { shell: '命令执行', files: '文件访问', input: '键鼠操作', screen: '屏幕查看', browser: '网页操作' }
const deviceCapabilityText = computed(() => (deviceDetail.value?.capabilities || []).map((capability) =>
  (capabilityNames[capability] || capability) + (deviceDetail.value.available?.[capability] === false ? '（暂不可用）' : '')
).join('、'))
/** 设备摘要只读取当前工具回执，不从相邻工具或会话猜测设备。 */
const headerDevices = computed(() => {
  if (deviceDetail.value) return [deviceDetail.value]
  let devices = []
  if (toolId.value === 'devices_list') devices = receipt.value?.devices || []
  else if (toolId.value === 'session_open' && receipt.value?.device) devices = [receipt.value.device]
  return devices.map((device) => ({ ...device, ...deviceAppearance(device) }))
})
const sessionCapabilities = computed(() =>
  toolId.value === 'session_open' ? receipt.value?.capabilities ?? args.value.capabilities ?? [] : [])
const sessionCapabilityText = computed(() => {
  return sessionCapabilities.value.map((capability) => capabilityNames[capability] || capability).join('、')
})
const actionIcon = computed(() => {
  if (['computer_act', 'browser_act'].includes(toolId.value)) {
    const type = args.value.action?.type
    if (['type', 'key', 'fill', 'press'].includes(type)) return Keyboard
    if (['click', 'double_click'].includes(type)) return MousePointerClick
  }
  return null
})
const observation = computed(() => result.value?.observation ||
  (['computer_observe', 'browser_observe'].includes(toolId.value) ? result.value : null))
const actionNames = { click: '点击', double_click: '双击', type: '输入文本', key: '按键', scroll: '滚动', drag: '拖动' }
const actionLabel = computed(() => {
  if (toolId.value === 'session_open' && sessionCapabilities.value.includes('browser')) return '连接浏览器'
  if (toolId.value === 'command_status' && display.value.browserScriptResult) return '查看浏览器执行结果'
  if (toolId.value === 'browser_act') {
    const names = { click: '点击网页元素', fill: '填写网页内容', press: '网页按键', select: '选择网页选项', hover: '悬停网页元素', scroll: '滚动网页' }
    return names[args.value.action?.type] || ABRIDGE_TOOL_NAMES.browser_act
  }
  return toolId.value === 'computer_act'
    ? actionNames[args.value.action?.type] || ABRIDGE_TOOL_NAMES.computer_act
    : ABRIDGE_TOOL_NAMES[toolId.value]
})
const operationStopLabel = computed(() => toolId.value === 'operation_status'
  ? { refused: '操作被拒绝', cancelled: '操作已取消' }[receipt.value?.status] : '')
const target = computed(() => {
  const input = args.value
  if (deviceDetail.value) return deviceDetail.value.name || '未命名设备'
  if (toolId.value === 'session_close')
    return status.value === 'completed' && receipt.value?.status === 'stopped' ? '连接已关闭' : ''
  if (toolId.value === 'session_open')
    return [receipt.value?.device?.name || input.device_id, sessionCapabilityText.value].filter(Boolean).join(' · ')
  if (['browser_open', 'browser_observe', 'browser_act'].includes(toolId.value)) {
    return observation.value?.title || result.value?.title || observation.value?.url || result.value?.url ||
      input.url || input.action?.element_ref || input.query || ''
  }
  if (toolId.value === 'browser_read') return result.value?.source_url || input.selector || ''
  if (toolId.value === 'browser_wait') return input.selector || input.text || ''
  if (toolId.value === 'browser_exec') return ['accepted', 'dispatched'].includes(receipt.value?.status) ? '脚本处理中' : ''
  if (toolId.value === 'command_execute') return command.value
  if (toolId.value === 'operation_status') {
    if (status.value !== 'completed') return ''
    if (receipt.value?.result_available === false) return '操作结果已过期'
    return operationStopLabel.value || { accepted: '操作已受理', dispatched: '操作处理中', executed: '操作已执行' }[receipt.value?.status]
  }
  if (toolId.value === 'command_status') {
    if (receipt.value?.result_available === false) return '输出已过期'
    if (['accepted', 'dispatched'].includes(receipt.value?.status)) return '命令处理中'
    return receipt.value?.status === 'executed' ? '命令已结束' : ''
  }
  if (['files_list', 'file_write', 'file_read'].includes(toolId.value)) return result.value?.path || input.path || ''
  if (toolId.value === 'computer_act') {
    const action = input.action || {}
    if (action.type === 'type') return action.text || ''
    if (action.type === 'key') return [...(action.modifiers || []), action.key].filter(Boolean).join('+')
    if (action.x != null && action.y != null) return `(${action.x}, ${action.y})`
    return action.element_ref || ''
  }
  if (toolId.value === 'devices_list' && receipt.value?.devices) {
    const names = headerDevices.value.map((device) => device.name || '未命名设备').join('、')
    return [`${headerDevices.value.length} 台设备`, names].filter(Boolean).join(' · ')
  }
  return observation.value?.app_name || input.query || input.bundle_id || input.device_id || ''
})
</script>

<style lang="less" scoped>
.device-summary,
.device-item {
  display: inline-flex;
  align-items: center;
  min-width: 0;
}
.device-summary { gap: 10px; }
.device-count { flex-shrink: 0; }
.session-capabilities { flex-shrink: 0; }
.device-item {
  gap: 4px;
  img,
  svg {
    width: 14px;
    height: 14px;
    flex-shrink: 0;
  }
}
.device-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.device-detail {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 16px;
  margin: 0;
  font-size: 12px;
  line-height: 20px;
  dt { color: var(--gray-500); }
  dd { margin: 0; color: var(--gray-700); overflow-wrap: anywhere; }
}
.device-file-list,
.device-file-summary {
  font-size: 12px;
  line-height: 20px;
}
.device-file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  max-height: 300px;
  overflow: auto;
  margin: 0;
  padding: 0;
  list-style: none;
  li {
    display: inline-flex;
    align-items: center;
    min-width: 0;
    max-width: 100%;
    gap: 4px;
  }
}
.device-file-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.device-file-summary {
  margin: 0;
  color: var(--gray-600);
}
.device-file-content {
  max-height: 300px;
  overflow: auto;
  margin: 0;
  padding: 10px;
  border-radius: 4px;
  color: var(--tool-result-text-color, var(--gray-700));
  background: var(--gray-25);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.4;
}
.browser-table {
  max-width: 100%;
  max-height: 300px;
  overflow: auto;
  font-size: 12px;
  table { border-collapse: collapse; }
  caption { caption-side: top; color: var(--gray-600); text-align: left; padding-bottom: 4px; white-space: nowrap; }
  td { border: 1px solid var(--gray-150); padding: 4px 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
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
