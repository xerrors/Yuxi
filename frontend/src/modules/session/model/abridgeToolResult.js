export const ABRIDGE_TOOL_NAMES = {
  devices_list: '查看设备',
  session_open: '连接设备',
  command_execute: '执行设备命令',
  computer_apps: '查找应用',
  computer_windows: '查看应用窗口',
  computer_open: '打开应用',
  computer_observe: '查看屏幕',
  computer_act: '操作电脑',
  browser_open: '打开网页',
  browser_observe: '查看网页',
  browser_act: '操作网页'
}

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
