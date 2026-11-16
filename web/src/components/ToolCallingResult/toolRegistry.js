import {
  BookOpen,
  Bot,
  Brain,
  Calculator,
  CheckSquare,
  Database,
  FileEdit,
  FilePen,
  FileText,
  Folder,
  FolderOutput,
  FolderSearch,
  Globe,
  HelpCircle,
  Image,
  Network,
  RefreshCw,
  SquareTerminal
} from '@lucide/vue'

export const TOOL_ICON_MAP = {
  ask_user_question: HelpCircle,
  bash: SquareTerminal,
  calculator: Calculator,
  cmd: SquareTerminal,
  edit_file: FilePen,
  execute: SquareTerminal,
  find_kb_document: FolderSearch,
  get_mindmap: Network,
  glob: FolderSearch,
  grep: FolderSearch,
  list_directory: Folder,
  list_kbs: BookOpen,
  ls: Folder,
  mysql_describe_table: Database,
  mysql_list_tables: Database,
  mysql_query: Database,
  ocr_parse_file: FileText,
  open_kb_document: FileText,
  present_artifacts: FolderOutput,
  query_kb: BookOpen,
  read_file: FileText,
  remember_memory: Brain,
  replace: FilePen,
  run_shell_command: SquareTerminal,
  search_file: FolderSearch,
  search_file_content: FolderSearch,
  subagent_await: Bot,
  subagent_cancel: Bot,
  subagent_events: RefreshCw,
  subagent_start: Bot,
  subagent_status: RefreshCw,
  task: Bot,
  web_search: Globe,
  tavily_search: Globe,
  doubao_search: Globe,
  text_to_img_qwen_image: Image,
  write_file: FileEdit,
  write_todos: CheckSquare
}

// 前端兜底的工具显示名称：仅用于工具列表（availableTools）无法映射到 display name 的工具，
// 例如 FilesystemMiddleware / TodoListMiddleware 等 middleware 注入的工具。
// 内置工具与知识库工具的 display_name 由后端定义（@tool 装饰器），通过工具列表下发。
export const TOOL_NAME_MAP = {
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
  write_todos: '更新任务清单',
  task: '调用子智能体',
  subagent_start: '启动子智能体',
  subagent_status: '查询子智能体',
  subagent_events: '查看子智能体事件',
  subagent_cancel: '取消子智能体',
  subagent_await: '等待子智能体',
  text_to_img_qwen_image: '生成图片',
  query_kb: '搜索知识库',
  list_kbs: '查看知识库列表',
  find_kb_document: '查找知识库文档',
  open_kb_document: '打开知识库文档',
  get_mindmap: '获取思维导图',
  calculator: '计算器',
  web_search: '网络搜索',
  tavily_search: '网络搜索',
  doubao_search: '网络搜索',
  ocr_parse_file: 'OCR识别文件',
  mysql_list_tables: '列出数据库表',
  mysql_describe_table: '查看表结构',
  mysql_query: '执行SQL查询',
  ask_user_question: '向用户提问'
}

// Keep intentionally hidden tool calls centralized so group summaries and renderers stay consistent.
export const HIDDEN_TOOL_CALL_IDS = ['present_artifacts']

export const getToolCallId = (toolCall) => toolCall?.name || toolCall?.function?.name || ''

export const getToolName = (toolId) => TOOL_NAME_MAP[toolId] || toolId

// 从工具元数据列表（完整工具列表或 buildin options）中按工具 id 查找对应元数据
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

export const SUBAGENT_TOOL_IDS = [
  'task',
  'subagent_start',
  'subagent_status',
  'subagent_events',
  'subagent_cancel',
  'subagent_await'
]

export const isSubagentToolCall = (toolCall) => SUBAGENT_TOOL_IDS.includes(getToolCallId(toolCall))

export const parseToolCallResult = (toolCall) => {
  const content = toolCall?.tool_call_result?.content ?? toolCall?.result
  if (content == null || content === '') return null
  if (typeof content === 'object') return content
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

/** 以调用、ToolMessage 和结果顶层的错误状态优先决定工具展示状态。 */
export const getToolCallStatus = (toolCall) => {
  const statuses = [
    toolCall?.status,
    toolCall?.tool_call_result?.status,
    parseToolCallResult(toolCall)?.status
  ]
  if (statuses.some((status) => status === 'error' || status === 'failed')) return 'error'
  if (
    toolCall?.tool_call_result != null ||
    toolCall?.result != null ||
    toolCall?.status === 'success' ||
    toolCall?.status === 'completed'
  )
    return 'completed'
  return 'running'
}

/** 子智能体结果与补充运行信息中的状态，供详情和分组共同展示。 */
export const getSubagentRunStatus = (toolCall) => {
  if (getToolCallStatus(toolCall) === 'error') return 'error'
  const result = parseToolCallResult(toolCall)
  return (
    result?.run_status ||
    result?.active_run_status ||
    result?.status ||
    toolCall?.subagent_run?.status ||
    ''
  )
}

/** 统一工具行与分组的展示状态，保留子智能体特有的运行态。 */
export const getToolCallDisplayStatus = (toolCall, activeSubagentToolCallIds) => {
  const status = getToolCallStatus(toolCall)
  if (status === 'error' || !isSubagentToolCall(toolCall)) return status
  const runStatus = getSubagentRunStatus(toolCall)
  if (['error', 'failed', 'cancelled', 'interrupted'].includes(runStatus)) return 'error'
  if (getToolCallId(toolCall) === 'task') {
    if (status === 'completed') return 'completed'
    return activeSubagentToolCallIds?.has(String(toolCall.id)) ? 'running' : 'completed'
  }
  if (['failed', 'cancelled', 'interrupted'].includes(runStatus)) return 'error'
  if (status === 'completed' || runStatus === 'completed' || parseToolCallResult(toolCall)?.status)
    return 'completed'
  return 'running'
}

export const enrichSubagentToolCall = (
  toolCall,
  { subagentRunById, subagentRunByThreadId, subagentOptionBySlug } = {}
) => {
  if (!isSubagentToolCall(toolCall)) return toolCall

  const args = parseToolCallArgs(toolCall)
  const result = parseToolCallResult(toolCall)
  const subagentRun =
    (toolCall.id ? subagentRunById?.get?.(String(toolCall.id)) : null) ||
    (result?.run_id ? subagentRunById?.get?.(String(result.run_id)) : null) ||
    (args.thread_id ? subagentRunByThreadId?.get?.(String(args.thread_id)) : null) ||
    (result?.thread_id ? subagentRunByThreadId?.get?.(String(result.thread_id)) : null)
  const subagentOption = args.subagent_slug
    ? subagentOptionBySlug?.get?.(String(args.subagent_slug))
    : null
  const displayLabel =
    result?.subagent_name ||
    subagentRun?.subagent_name ||
    subagentOption?.name ||
    result?.subagent_slug ||
    subagentRun?.subagent_slug ||
    undefined

  return {
    ...toolCall,
    ...(subagentRun ? { subagent_run: subagentRun } : {}),
    ...(displayLabel ? { display_label: displayLabel } : {})
  }
}

export const normalizeToolCalls = (toolCalls, { includeHidden = false, mapToolCall } = {}) => {
  if (!Array.isArray(toolCalls)) return []

  return toolCalls
    .filter((toolCall) => {
      if (!isValidToolCall(toolCall)) return false
      return includeHidden || !isHiddenToolCall(toolCall)
    })
    .map((toolCall) => (mapToolCall ? mapToolCall(toolCall) : toolCall))
}

export const enrichTaskToolCalls = (toolCalls, options = {}) =>
  normalizeToolCalls(toolCalls, {
    mapToolCall: (toolCall) => enrichSubagentToolCall(toolCall, options)
  })

export const getToolIcon = (toolId) => TOOL_ICON_MAP[toolId] || null
