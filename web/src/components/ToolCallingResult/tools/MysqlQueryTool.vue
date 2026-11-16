<template>
  <BaseToolCall :tool-call="toolCall">
    <template #header>
      <div class="sep-header">
        <span class="note">执行SQL查询</span>
        <span class="separator" v-if="sqlText">|</span>
        <span class="description code" v-if="sqlText">{{ truncateSql(sqlText) }}</span>
      </div>
    </template>

    <template #params="{ args }">
      <div class="mysql-params">
        <pre class="sql-text">{{ extractSql(args) }}</pre>
      </div>
    </template>

    <template #result="{ resultContent }">
      <div class="mysql-result">
        <pre class="result-text">{{ formatMysqlResult(resultContent) }}</pre>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { formatMysqlResult } from './mysqlResultFormatter.js'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const sqlText = computed(() =>
  extractSql(props.toolCall.args || props.toolCall.function?.arguments)
)

const extractSql = (args) => {
  if (!args) return ''

  // 解析 args（可能是字符串或对象）
  let parsedArgs = args
  if (typeof args === 'string') {
    try {
      parsedArgs = JSON.parse(args)
    } catch {
      return args
    }
  }

  // 提取 sql 字段
  const sql = parsedArgs?.sql || parsedArgs?.query
  return sql || JSON.stringify(parsedArgs, null, 2)
}

const truncateSql = (sql, maxLength = 50) => {
  if (!sql) return ''
  // 移除换行符和多余空格
  const singleLine = sql.replace(/\s+/g, ' ').trim()
  if (singleLine.length <= maxLength) return singleLine
  return singleLine.slice(0, maxLength) + '...'
}
</script>

<style lang="less" scoped>
.mysql-params {
  .sql-text {
    margin: 0;
    font-size: 11px;
    line-height: 1.4;
    color: var(--gray-800);
    white-space: pre-wrap;
    word-break: break-word;
    padding: 4px;
    border-radius: 4px;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    max-height: 200px;
    overflow-y: auto;
  }
}

.mysql-result {
  border-radius: 8px;
  padding: 4px;

  .result-text {
    margin: 0;
    font-size: 12px;
    line-height: 1.4;
    color: var(--gray-700);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 400px;
    overflow-y: auto;
    background: var(--gray-50);
    padding: 10px;
    border-radius: 4px;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  }
}
</style>
