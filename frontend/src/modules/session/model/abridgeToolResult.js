export const ABRIDGE_TOOL_NAMES = {
  devices_list: '查看设备',
  device_get: '查看设备详情',
  session_open: '连接设备',
  session_close: '断开设备连接',
  command_execute: '执行设备命令',
  command_status: '查看命令输出',
  operation_status: '查看操作状态',
  operation_cancel: '取消设备操作',
  command_cancel: '取消设备命令',
  files_list: '列出设备目录',
  file_write: '写入设备文件',
  file_read: '读取设备文件',
  computer_apps: '查找应用',
  computer_windows: '查看应用窗口',
  computer_open: '打开应用',
  computer_observe: '查看屏幕',
  computer_act: '操作电脑',
  browser_open: '打开网页',
  browser_observe: '查看网页',
  browser_read: '读取网页内容',
  browser_wait: '等待网页条件',
  browser_exec: '执行浏览器脚本',
  browser_act: '操作网页'
}

export const ABRIDGE_OPERATION_RECEIPT_TOOLS = ['operation_status', 'operation_cancel']

/** 从 aBridge 的文本与截图混合回执中读取结构化执行结果。 */
export function parseAbridgeResult(content) {
  try {
    const value = typeof content === 'string' ? JSON.parse(content) : content
    if (!Array.isArray(value)) return value && typeof value === 'object' ? value : null
    const text = value.find((part) => ['text', 'input_text'].includes(part?.type))?.text
    return text ? JSON.parse(text) : null
  } catch {
    return null
  }
}

/** 将当前回执投影为展示内容；工具完成不代表远端操作已成功。 */
export function projectAbridgeResult({ toolId, content, status, parts }) {
  const receipt = parseAbridgeResult(content)
  const result = receipt?.result
  const succeeded = status === 'completed' && receipt?.status === 'executed'
  const operationReceipt = ABRIDGE_OPERATION_RECEIPT_TOOLS.includes(toolId)
  // 查询和取消操作都可返回浏览器脚本或命令输出，按回执字段选择展示。
  const browserScriptResult = (operationReceipt || ['browser_exec', 'command_status'].includes(toolId)) &&
    Number.isInteger(result?.steps_completed) ? result : null
  const streams = result ?? receipt?.output
  const isCommandResult = !browserScriptResult && (['command_execute', 'command_status'].includes(toolId) ||
    (operationReceipt && (typeof streams?.stdout === 'string' || typeof streams?.stderr === 'string')))
  const commandResult = isCommandResult ? streams : null
  const output = commandResult || {}
  const commandOutput = isCommandResult
    ? [
        output.stdout,
        output.stderr && `标准错误：\n${output.stderr}`,
        (output.truncated || receipt?.output_truncated || receipt?.capture_truncated) && '输出已截断',
        output.timed_out && '命令执行超时',
        !operationReceipt && receipt?.result_available === false && '命令输出已过期'
      ].filter(Boolean).join('\n') : ''
  const fileResult = succeeded && ['files_list', 'file_write', 'file_read'].includes(toolId) ? result : null
  const browserReadResult = succeeded && toolId === 'browser_read' && (
    (result?.mode === 'text' && typeof result.text === 'string') ||
    (result?.mode === 'tables' && Array.isArray(result.rows))
  ) ? result : null
  const browserTables = Object.create(null)
  for (const row of browserReadResult?.rows || []) {
    (browserTables[row.table] ??= []).push(row)
  }
  let browserReadSummary = ''
  if (browserReadResult) {
    const value = browserReadResult
    const size = value.mode === 'tables' ? value.rows.length : value.text.length
    const unit = value.mode === 'tables' ? '行' : '字符'
    const coverage = { loaded_same_origin_frame: '已加载的同源框架', loaded_main_document: '已加载的主文档' }[value.coverage] || '读取范围未说明'
    browserReadSummary = `${coverage} · ${value.offset + size}/${value.total} ${unit}${value.cached ? ' · 已保存快照' : ''}`
  }
  const browserWaitMatched = succeeded && toolId === 'browser_wait' && result?.matched === true
  let browserScriptSummary = ''
  let browserScriptValue
  if (browserScriptResult) {
    const value = browserScriptResult
    const logs = Array.isArray(value.output) ? value.output.join('\n') : ''
    browserScriptSummary = [`已完成 ${value.steps_completed} 个步骤`, !operationReceipt && receipt?.error?.message, logs && `日志：\n${logs}`].filter(Boolean).join('\n')
    if (succeeded) {
      if (value.value_truncated) browserScriptValue = value.value_preview
      else if (Object.hasOwn(value, 'value')) browserScriptValue = typeof value.value === 'string' ? value.value : JSON.stringify(value.value, null, 2)
    }
  }
  const error = receipt?.error
  const operationError = operationReceipt
    ? [typeof error === 'string' ? error : error?.message, error?.cause?.message]
        .filter((message) => typeof message === 'string' && message).join('\n')
    : ''
  return {
    operationReceipt, operationError, receipt, commandResult, commandOutput, fileResult,
    browserReadResult, browserReadSummary, browserTables, browserWaitMatched,
    browserScriptResult, browserScriptSummary, browserScriptValue,
    rawResult: parts.filter((part) => !part.image).map((part) => part.text).join('\n'),
    images: parts.filter((part) => part.image).map((part) => ({ type: 'image_url', image_url: { url: part.src } }))
  }
}
