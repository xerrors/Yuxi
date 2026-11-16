<template>
  <component
    :is="currentRenderer"
    v-if="currentRenderer"
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
    ref="toolRendererRef"
  />
  <BaseToolCall
    v-else-if="!isHidden"
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
  />
</template>

<script setup>
import { computed, ref } from 'vue'
import BaseToolCall from './BaseToolCall.vue'

import WebSearchTool from './tools/WebSearchTool.vue'
import ListKbsTool from './tools/ListKbsTool.vue'
import GetMindmapTool from './tools/GetMindmapTool.vue'
import QueryKbTool from './tools/QueryKbTool.vue'
import FindKbDocumentTool from './tools/FindKbDocumentTool.vue'
import OpenKbDocumentTool from './tools/OpenKbDocumentTool.vue'
import CalculatorTool from './tools/CalculatorTool.vue'
import TodoListTool from './tools/TodoListTool.vue'
import TaskTool from './tools/TaskTool.vue'
import SubagentLifecycleTool from './tools/SubagentLifecycleTool.vue'
import ImageTool from './tools/ImageTool.vue'
import WriteFileTool from './tools/WriteFileTool.vue'
import ReadFileTool from './tools/ReadFileTool.vue'
import ListDirectoryTool from './tools/ListDirectoryTool.vue'
import SearchFileContentTool from './tools/SearchFileContentTool.vue'
import SearchFileTool from './tools/SearchFileTool.vue'
import GrepTool from './tools/GrepTool.vue'
import GlobTool from './tools/GlobTool.vue'
import EditFileTool from './tools/EditFileTool.vue'
import MysqlQueryTool from './tools/MysqlQueryTool.vue'
import MysqlDescribeTableTool from './tools/MysqlDescribeTableTool.vue'
import MysqlListTablesTool from './tools/MysqlListTablesTool.vue'
import AskUserQuestionTool from './tools/AskUserQuestionTool.vue'
import ExecuteTool from './tools/ExecuteTool.vue'
import OcrParseFileTool from './tools/OcrParseFileTool.vue'
import RememberMemoryTool from './tools/RememberMemoryTool.vue'
import { getToolCallId, isHiddenToolCall } from './toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  },
  appearance: {
    type: String,
    default: 'card'
  },
  defaultExpanded: {
    type: Boolean,
    default: false
  }
})

const toolId = computed(() => getToolCallId(props.toolCall))

const TOOL_RENDERERS = {
  ask_user_question: AskUserQuestionTool,
  bash: ExecuteTool,
  calculator: CalculatorTool,
  cmd: ExecuteTool,
  edit_file: EditFileTool,
  execute: ExecuteTool,
  find_kb_document: FindKbDocumentTool,
  get_mindmap: GetMindmapTool,
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
  subagent_await: SubagentLifecycleTool,
  subagent_cancel: SubagentLifecycleTool,
  subagent_events: SubagentLifecycleTool,
  subagent_start: SubagentLifecycleTool,
  subagent_status: SubagentLifecycleTool,
  task: TaskTool,
  web_search: WebSearchTool,
  tavily_search: WebSearchTool,
  doubao_search: WebSearchTool,
  text_to_img_qwen_image: ImageTool,
  write_file: WriteFileTool,
  write_todos: TodoListTool
}

const currentRenderer = computed(() => TOOL_RENDERERS[toolId.value] || null)
const isHidden = computed(() => isHiddenToolCall(props.toolCall))

const toolRendererRef = ref(null)
const refreshGraph = () => {
  if (toolRendererRef.value && typeof toolRendererRef.value.refreshGraph === 'function') {
    toolRendererRef.value.refreshGraph()
  }
}

defineExpose({ refreshGraph })
</script>

<style lang="less" scoped></style>
