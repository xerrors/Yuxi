export const TOOL_NAME_MAP = {
  create_session: '创建会话',
  send_message: '投递协作消息',
  submit_input: '提交工作',
  cancel_turn: '取消轮次',
  wait_sessions: '等待协作更新',
  wait_inputs: '等待任务结果',
  list_sessions: '查看会话',
  list_agents: '查看智能体',
  get_result: '查看任务结果',
  search_thread_messages: '搜索历史消息',
  read_thread_messages: '读取历史消息',
  install_skill: '安装技能',
  mark_for_improvement: '记录改进线索',
  bash: '执行命令',
  cmd: '执行命令',
  execute: '执行命令',
  run_shell_command: '执行命令',
  ls: '列出目录',
  list_directory: '列出目录',
  glob: '匹配文件路径',
  grep: '搜索文件内容',
  read_file: '读取文件',
  remember_memory: '更新记忆',
  write_file: '写入文件',
  edit_file: '编辑文件',
  replace: '编辑文件',
  search_file: '搜索知识库文件',
  search_file_content: '搜索文件内容',
  text_to_img_qwen_image: '生成图片',
  query_kb: '搜索知识库',
  list_kbs: '查看知识库列表',
  find_kb_document: '查找知识库文档',
  open_kb_document: '打开知识库文档',
  calculator: '计算器',
  web_search: '网络搜索',
  web_fetch: '网页抓取',
  tavily_search: '网络搜索',
  doubao_search: '网络搜索',
  ocr_parse_file: 'OCR识别文件',
  mysql_list_tables: '查看数据库表',
  mysql_describe_table: '查看表结构',
  mysql_query: '执行SQL查询',
  ask_user_question: '向用户提问'
}

// Keep intentionally hidden tool calls centralized so group summaries and renderers stay consistent.
export const HIDDEN_TOOL_CALL_IDS = ['present_artifacts']

const IMAGE_RESULT_TYPES = new Set(['image', 'image_url', 'input_image'])
const TEXT_RESULT_TYPES = new Set(['text', 'input_text'])

export const getToolCallId = (toolCall) => toolCall?.name || toolCall?.function?.name || ''

export const getToolName = (toolId) => TOOL_NAME_MAP[toolId] || toolId

// 从工具元数据列表（完整工具列表或 builtin options）中按工具 id 查找对应元数据
export const findToolInList = (toolId, toolsList) =>
  (toolsList || []).find((t) => (t.slug ?? t.key ?? t.id) === toolId)

export const isHiddenToolCall = (toolCall) => HIDDEN_TOOL_CALL_IDS.includes(getToolCallId(toolCall))

export const isValidToolCall = (toolCall) => {
  return Boolean(
    toolCall &&
    (toolCall.id || toolCall.name || toolCall.function?.name) &&
    (toolCall.args !== undefined ||
      toolCall.function?.arguments !== undefined ||
      toolCall.tool_call_result !== undefined)
  )
}

export const parseToolCallArgs = (toolCall) => {
  const args = toolCall?.args ?? toolCall?.function?.arguments
  if (!args) return {}
  if (typeof args === 'object') return args
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
}

/** 统一读取实时 ToolMessage 与历史结果的正文。 */
export const getToolCallResultContent = (toolCall) =>
  toolCall?.tool_call_result?.content ?? toolCall?.result

export const parseToolCallResult = (toolCall) => {
  const content = getToolCallResultContent(toolCall)
  if (content == null || content === '') return null
  return parseResultValue(content, null)
}

/** 保留普通 JSON，按顺序投影文本与图片；裸 URL 仅由图片生成工具启用。 */
export function getToolResultParts(content, { imageUrls = false } = {}) {
  const value = parseResultValue(content, content)
  if (
    typeof value === 'string' &&
    (value.startsWith('data:image/') || (imageUrls && /^(https?:\/\/|\/(?!\/))/i.test(value)))
  ) {
    return [{ image: true, src: getImageSource({ url: value }) }]
  }
  if (
    !Array.isArray(value) ||
    !value.some((part) => IMAGE_RESULT_TYPES.has(part?.type) || TEXT_RESULT_TYPES.has(part?.type))
  ) {
    return [{ text: formatResultText(value) }]
  }
  return value.map((part) => {
    if (IMAGE_RESULT_TYPES.has(part?.type)) return { image: true, src: getImageSource(part) }
    return { text: TEXT_RESULT_TYPES.has(part?.type) ? part.text : formatResultText(part) }
  })
}

/**
 * 解开 MCP 文本信封（[{type:'text'|'input_text', text}]）并把内嵌 JSON 解析为结构化值。
 * 知识库等经 MCP 调用的工具结果以信封持久化，专用渲染器需要先解包再读取字段。
 */
export function unwrapToolResultContent(content) {
  let value = typeof content === 'string' ? parseResultValue(content, content) : content
  if (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (part) =>
        part &&
        typeof part === 'object' &&
        TEXT_RESULT_TYPES.has(part.type) &&
        typeof part.text === 'string'
    )
  ) {
    const texts = value.map((part) => parseResultValue(part.text, part.text))
    return texts.length === 1 ? texts[0] : texts
  }
  return value
}

/** 以调用、ToolMessage 和结果顶层的错误状态优先决定工具展示状态。 */
export const getToolCallStatus = (toolCall) => {
  const result = parseToolCallResult(toolCall)
  const statuses = [toolCall?.status, toolCall?.tool_call_result?.status, result?.status]
  if (statuses.some((status) => status === 'error' || status === 'failed')) return 'error'
  // 已保存的旧搜索失败只有 error 正文，没有错误状态。
  if (
    ['web_search', 'tavily_search', 'doubao_search'].includes(getToolCallId(toolCall)) &&
    typeof result?.error === 'string' &&
    result.error.trim()
  ) {
    return 'error'
  }
  if (statuses.includes('waiting')) return 'waiting'
  if (statuses.includes('incomplete')) return 'incomplete'
  if (
    toolCall?.tool_call_result != null ||
    toolCall?.result != null ||
    toolCall?.status === 'success' ||
    toolCall?.status === 'completed'
  )
    return 'completed'
  return 'running'
}

export const getToolCallDisplayStatus = (toolCall) => getToolCallStatus(toolCall)

export const normalizeToolCalls = (toolCalls, { includeHidden = false, mapToolCall } = {}) => {
  if (!Array.isArray(toolCalls)) return []

  return toolCalls
    .filter((toolCall) => {
      if (!isValidToolCall(toolCall)) return false
      return includeHidden || !isHiddenToolCall(toolCall)
    })
    .map((toolCall) => (mapToolCall ? mapToolCall(toolCall) : toolCall))
}

/** 保持既有解析失败契约，展示调用方可显式保留原始文本。 */
function parseResultValue(content, fallback) {
  if (typeof content !== 'string') return content
  try {
    return JSON.parse(content)
  } catch {
    return fallback
  }
}

/** 图片只接受 HTTP(S)、图片编码或同源根相对地址。 */
function getImageSource(block) {
  const base64 = block.base64 ?? block.data
  const mime = block.mime_type ?? block.mimeType
  let source = block.url ?? block.image_url?.url ?? block.image_url
  if (typeof base64 === 'string' && /^image\/[\w.+-]+$/.test(mime || '')) {
    source = `data:${mime};base64,${base64}`
  }
  if (typeof source !== 'string') return ''
  return /^(https?:\/\/|data:image\/[\w.+-]+;base64,|\/(?!\/))/i.test(source) ? source : ''
}

/** 普通对象保留完整 JSON，图片编码不进入文本分支。 */
function formatResultText(value) {
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}
