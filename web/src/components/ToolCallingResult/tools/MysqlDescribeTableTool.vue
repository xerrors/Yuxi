<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">查看表结构</span>
        <span class="separator" v-if="tableName">|</span>
        <span class="description code" v-if="tableName">{{ tableName }}</span>
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

const tableName = computed(() =>
  extractTableName(props.toolCall.args || props.toolCall.function?.arguments)
)

const extractTableName = (args) => {
  if (!args) return ''

  let parsedArgs = args
  if (typeof args === 'string') {
    try {
      parsedArgs = JSON.parse(args)
    } catch {
      return args
    }
  }

  return parsedArgs?.table_name || ''
}
</script>

<style lang="less" scoped>
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
