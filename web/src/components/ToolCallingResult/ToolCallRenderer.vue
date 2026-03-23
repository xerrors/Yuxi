<template>
  <!-- 知识图谱查询结果 -->
  <KnowledgeGraphTool v-if="isKnowledgeGraphResult" :tool-call="toolCall" ref="graphToolCallRef" />

  <!-- 网页搜索 -->
  <WebSearchTool v-else-if="isWebSearchResult" :tool-call="toolCall" />

  <!-- Chart -->
  <ChartTool v-else-if="isChartResult" :tool-call="toolCall" />

  <!-- 知识库列表 -->
  <ListKbsTool v-else-if="toolName === 'list_kbs'" :tool-call="toolCall" />

  <!-- 思维导图 -->
  <GetMindmapTool v-else-if="toolName === 'get_mindmap'" :tool-call="toolCall" />

  <!-- 知识库检索 -->
  <QueryKbTool v-else-if="toolName === 'query_kb'" :tool-call="toolCall" />

  <!-- 待办事项 -->
  <TodoListTool v-else-if="toolName === 'write_todos'" :tool-call="toolCall" />

  <!-- 计算器 -->
  <CalculatorTool v-else-if="isCalculatorResult" :tool-call="toolCall" />

  <!-- 图片 -->
  <ImageTool v-else-if="isImageResult" :tool-call="toolCall" />

  <!-- 任务分配 -->
  <TaskTool v-else-if="isTaskResult" :tool-call="toolCall" />

  <!-- 写文件 -->
  <WriteFileTool v-else-if="toolName === 'write_file'" :tool-call="toolCall" />

  <!-- 读文件 -->
  <ReadFileTool v-else-if="toolName === 'read_file'" :tool-call="toolCall" />

  <!-- 列目录 -->
  <ListDirectoryTool
    v-else-if="toolName === 'list_directory' || toolName === 'ls'"
    :tool-call="toolCall"
  />

  <!-- 搜索文件内容 -->
  <SearchFileContentTool v-else-if="toolName === 'search_file_content'" :tool-call="toolCall" />

  <!-- Glob 搜索 -->
  <GlobTool v-else-if="toolName === 'glob'" :tool-call="toolCall" />

  <!-- 编辑文件 -->
  <EditFileTool
    v-else-if="toolName === 'edit_file' || toolName === 'replace'"
    :tool-call="toolCall"
  />

  <!-- MySQL 查询 -->
  <MysqlQueryTool v-else-if="toolName === 'mysql_query'" :tool-call="toolCall" />

  <!-- MySQL 描述表 -->
  <MysqlDescribeTableTool v-else-if="toolName === 'mysql_describe_table'" :tool-call="toolCall" />

  <!-- MySQL 列出表 -->
  <MysqlListTablesTool v-else-if="toolName === 'mysql_list_tables'" :tool-call="toolCall" />

  <!-- 向用户提问 -->
  <AskUserQuestionTool v-else-if="toolName === 'ask_user_question'" :tool-call="toolCall" />

  <!-- 执行命令 -->
  <ExecuteTool
    v-else-if="
      toolName === 'execute' ||
      toolName === 'run_shell_command' ||
      toolName === 'bash' ||
      toolName === 'cmd'
    "
    :tool-call="toolCall"
  />

  <!-- 默认展示 -->
  <BaseToolCall v-else :tool-call="toolCall" />
</template>

<script setup>
import { computed, ref } from 'vue'
import BaseToolCall from './BaseToolCall.vue'

import WebSearchTool from './tools/WebSearchTool.vue'
import ListKbsTool from './tools/ListKbsTool.vue'
import GetMindmapTool from './tools/GetMindmapTool.vue'
import QueryKbTool from './tools/QueryKbTool.vue'
import KnowledgeGraphTool from './tools/KnowledgeGraphTool.vue'
import ChartTool from './tools/ChartTool.vue'
import CalculatorTool from './tools/CalculatorTool.vue'
import TodoListTool from './tools/TodoListTool.vue'
import ImageTool from './tools/ImageTool.vue'
import TaskTool from './tools/TaskTool.vue'
import WriteFileTool from './tools/WriteFileTool.vue'
import ReadFileTool from './tools/ReadFileTool.vue'
import ListDirectoryTool from './tools/ListDirectoryTool.vue'
import SearchFileContentTool from './tools/SearchFileContentTool.vue'
import GlobTool from './tools/GlobTool.vue'
import EditFileTool from './tools/EditFileTool.vue'
import MysqlQueryTool from './tools/MysqlQueryTool.vue'
import MysqlDescribeTableTool from './tools/MysqlDescribeTableTool.vue'
import MysqlListTablesTool from './tools/MysqlListTablesTool.vue'
import AskUserQuestionTool from './tools/AskUserQuestionTool.vue'
import ExecuteTool from './tools/ExecuteTool.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const toolName = computed(() => props.toolCall.name || props.toolCall.function?.name || '')

const parseData = (content) => {
  if (typeof content === 'string') {
    try {
      return JSON.parse(content)
    } catch (error) {
      return content
    }
  }
  return content
}

// 识别逻辑
const isWebSearchResult = computed(() => {
  const name = toolName.value.toLowerCase()
  return name.includes('tavily_search')
})

const isTaskResult = computed(() => {
  let args = props.toolCall.args || props.toolCall.function?.arguments
  if (typeof args === 'string') {
    try {
      args = JSON.parse(args)
    } catch {
      return false
    }
  }
  return args && typeof args === 'object' && 'subagent_type' in args
})

const isKnowledgeGraphResult = computed(() => {
  const name = toolName.value.toLowerCase()
  const hasGraphKeyword = name.includes('graph') || name.includes('图谱') || name.includes('kg')
  const data = parseData(props.toolCall.tool_call_result?.content)
  const hasBasicStructure = data && typeof data === 'object'
  const hasTriples = hasBasicStructure && 'triples' in data
  return (
    (hasTriples && Array.isArray(data.triples) && data.triples.length > 0) ||
    (hasTriples && hasGraphKeyword)
  )
})

const isCalculatorResult = computed(() => {
  const name = toolName.value.toLowerCase()
  return name.includes('calculator') || name.includes('calc') || name.includes('math')
})

const isChartResult = computed(() => {
  const name = toolName.value.toLowerCase()
  if (!name.includes('chart')) return false
  const data = parseData(props.toolCall.tool_call_result?.content)
  // chart 返回数组格式 [{ type: "text", text: "url", id: "..." }]
  return Array.isArray(data) && data.length > 0 && data[0].type === 'text'
})

const isImageResult = computed(() => {
  const name = toolName.value.toLowerCase()
  if (!name.includes('text_to_img')) return false
  const data = parseData(props.toolCall.tool_call_result?.content)
  return data && typeof data === 'string' && data.startsWith('http')
})

// 处理知识图谱刷新
const graphToolCallRef = ref(null)
const refreshGraph = () => {
  if (graphToolCallRef.value && typeof graphToolCallRef.value.refreshGraph === 'function') {
    graphToolCallRef.value.refreshGraph()
  }
}

defineExpose({ refreshGraph })
</script>

<style lang="less" scoped></style>
