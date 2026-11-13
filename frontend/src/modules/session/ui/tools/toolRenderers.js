import { defineAsyncComponent } from 'vue'
import BaseToolCall from './BaseToolCall.vue'
import ToolRendererUnavailable from './ToolRendererUnavailable.vue'
import { ABRIDGE_TOOL_NAMES } from '../../model/abridgeToolResult.js'

/** 为工具详情提供基础卡片加载态和局部失败提示。 */
const createRenderer = (loader) => defineAsyncComponent({
  loader,
  loadingComponent: BaseToolCall,
  errorComponent: ToolRendererUnavailable,
  delay: 0,
  timeout: 10000,
  suspensible: false
})

export const CooperationTool = createRenderer(() => import('./renderers/CooperationTool.vue'))
const ListAgentsTool = createRenderer(() => import('./renderers/ListAgentsTool.vue'))
const ThreadMessagesTool = createRenderer(() => import('./renderers/ThreadMessagesTool.vue'))
const InstallSkillTool = createRenderer(() => import('./renderers/InstallSkillTool.vue'))
const ImprovementTool = createRenderer(() => import('./renderers/ImprovementTool.vue'))
const WebSearchTool = createRenderer(() => import('./renderers/WebSearchTool.vue'))
const WebFetchTool = createRenderer(() => import('./renderers/WebFetchTool.vue'))
const ListKbsTool = createRenderer(() => import('./renderers/ListKbsTool.vue'))
const QueryKbTool = createRenderer(() => import('./renderers/QueryKbTool.vue'))
const FindKbDocumentTool = createRenderer(() => import('./renderers/FindKbDocumentTool.vue'))
const OpenKbDocumentTool = createRenderer(() => import('./renderers/OpenKbDocumentTool.vue'))
const CalculatorTool = createRenderer(() => import('./renderers/CalculatorTool.vue'))
const ImageTool = createRenderer(() => import('./renderers/ImageTool.vue'))
const WriteFileTool = createRenderer(() => import('./renderers/WriteFileTool.vue'))
const ReadFileTool = createRenderer(() => import('./renderers/ReadFileTool.vue'))
const ListDirectoryTool = createRenderer(() => import('./renderers/ListDirectoryTool.vue'))
const SearchFileContentTool = createRenderer(() => import('./renderers/SearchFileContentTool.vue'))
const SearchFileTool = createRenderer(() => import('./renderers/SearchFileTool.vue'))
const GrepTool = createRenderer(() => import('./renderers/GrepTool.vue'))
const GlobTool = createRenderer(() => import('./renderers/GlobTool.vue'))
const EditFileTool = createRenderer(() => import('./renderers/EditFileTool.vue'))
const MysqlQueryTool = createRenderer(() => import('./renderers/MysqlQueryTool.vue'))
const MysqlDescribeTableTool = createRenderer(() => import('./renderers/MysqlDescribeTableTool.vue'))
const MysqlListTablesTool = createRenderer(() => import('./renderers/MysqlListTablesTool.vue'))
const AskUserQuestionTool = createRenderer(() => import('./renderers/AskUserQuestionTool.vue'))
const ExecuteTool = createRenderer(() => import('./renderers/ExecuteTool.vue'))
const AbridgeTool = createRenderer(() => import('./renderers/AbridgeTool.vue'))
const OcrParseFileTool = createRenderer(() => import('./renderers/OcrParseFileTool.vue'))
const RememberMemoryTool = createRenderer(() => import('./renderers/RememberMemoryTool.vue'))

export const TOOL_RENDERERS = {
  ...Object.fromEntries(Object.keys(ABRIDGE_TOOL_NAMES).map((name) => [name, AbridgeTool])),
  list_agents: ListAgentsTool,
  search_thread_messages: ThreadMessagesTool,
  read_thread_messages: ThreadMessagesTool,
  install_skill: InstallSkillTool,
  mark_for_improvement: ImprovementTool,
  ask_user_question: AskUserQuestionTool,
  bash: ExecuteTool,
  calculator: CalculatorTool,
  cmd: ExecuteTool,
  edit_file: EditFileTool,
  execute: ExecuteTool,
  find_kb_document: FindKbDocumentTool,
  glob: GlobTool,
  grep: GrepTool,
  list_directory: ListDirectoryTool,
  list_kbs: ListKbsTool,
  ls: ListDirectoryTool,
  mysql_describe_table: MysqlDescribeTableTool,
  mysql_list_tables: MysqlListTablesTool,
  mysql_query: MysqlQueryTool,
  ocr_parse_file: OcrParseFileTool,
  open_kb_document: OpenKbDocumentTool,
  query_kb: QueryKbTool,
  read_file: ReadFileTool,
  remember_memory: RememberMemoryTool,
  replace: EditFileTool,
  run_shell_command: ExecuteTool,
  search_file: SearchFileTool,
  search_file_content: SearchFileContentTool,
  web_search: WebSearchTool,
  web_fetch: WebFetchTool,
  tavily_search: WebSearchTool,
  doubao_search: WebSearchTool,
  text_to_img_qwen_image: ImageTool,
  write_file: WriteFileTool
}
