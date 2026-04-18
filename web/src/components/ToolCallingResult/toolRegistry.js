import {
  BookOpen,
  Bot,
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
  Terminal
} from 'lucide-vue-next'

export const TOOL_ICON_MAP = {
  ask_user_question: HelpCircle,
  bash: Terminal,
  calculator: Calculator,
  cmd: Terminal,
  edit_file: FilePen,
  execute: Terminal,
  get_mindmap: Network,
  glob: FolderSearch,
  grep: FolderSearch,
  list_directory: Folder,
  list_kbs: BookOpen,
  ls: Folder,
  mysql_describe_table: Database,
  mysql_list_tables: Database,
  mysql_query: Database,
  present_artifacts: FolderOutput,
  query_kb: BookOpen,
  query_knowledge_graph: Network,
  read_file: FileText,
  replace: FilePen,
  run_shell_command: Terminal,
  search_file_content: FolderSearch,
  task: Bot,
  tavily_search: Globe,
  searxng_search: Globe,
  text_to_img_qwen_image: Image,
  write_file: FileEdit,
  write_todos: CheckSquare
}

// Keep intentionally hidden tool calls centralized so group summaries and renderers stay consistent.
export const HIDDEN_TOOL_CALL_IDS = ['present_artifacts']

export const getToolCallId = (toolCall) => toolCall?.name || toolCall?.function?.name || ''

export const getToolIcon = (toolId) => TOOL_ICON_MAP[toolId] || null
