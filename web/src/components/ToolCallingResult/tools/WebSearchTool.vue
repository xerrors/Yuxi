<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">网络搜索</span>
        <span class="separator" v-if="query">|</span>
        <span class="description">{{ query }}</span>
      </div>
    </template>
    <template #result="{ resultContent }">
      <div class="web-search-result">
        <WebSearchResultList
          v-if="parsedData(resultContent).results && parsedData(resultContent).results.length > 0"
          :results="parsedData(resultContent).results"
        />

        <div v-else-if="parsedData(resultContent).rawText" class="raw-content">
          {{ parsedData(resultContent).rawText }}
        </div>

        <div v-else class="no-results">
          <p>未找到相关搜索结果</p>
        </div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import BaseToolCall from '../BaseToolCall.vue'
import WebSearchResultList from '@/components/sources/WebSearchResultList.vue'
import { computed } from 'vue'
import { parseToolCallArgs } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const parseData = (content) => {
  if (typeof content === 'string') {
    try {
      return JSON.parse(content)
    } catch {
      return { query: '', results: [], response_time: 0, rawText: content }
    }
  }
  return content || { query: '', results: [], response_time: 0 }
}

const parsedData = (content) => parseData(content)

const query = computed(() => {
  // First try to get it from result
  const result = parsedData(props.toolCall.tool_call_result?.content)
  if (result?.query) return result.query

  // Fallback to args
  const args = parseToolCallArgs(props.toolCall)
  return args?.query || args?.q || ''
})
</script>

<style lang="less" scoped>
.web-search-result {
  background: var(--gray-0);
  border-radius: 8px;

  .search-meta {
    padding: 12px 16px;
    background: var(--gray-25);
    display: flex;
    gap: 16px;
    font-size: 12px;
    color: var(--gray-600);
    border-bottom: 1px solid var(--gray-100);

    .query-text {
      font-weight: 500;
      color: var(--gray-800);
    }
  }

  .raw-content {
    padding: 12px;
    font-size: 13px;
    line-height: 1.5;
    color: var(--gray-700);
    white-space: pre-wrap;
    font-family: monospace;
  }

  .no-results {
    text-align: center;
    color: var(--gray-500);
    padding: 20px;
    font-size: 13px;
  }
}
</style>
