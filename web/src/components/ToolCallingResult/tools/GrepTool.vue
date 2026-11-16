<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">搜索文件内容</span>
        <span class="separator" v-if="pattern">|</span>
        <span class="note">{{ pattern }}</span>
        <span class="separator" v-if="targetPath">|</span>
        <span class="description code">{{ targetPath }}</span>
        <span class="tag" v-if="outputMode">{{ outputMode }}</span>
      </div>
    </template>

    <template #result>
      <div class="tool-result-renderer grep-result">
        <div class="result-summary" v-if="matchCountLabel">
          <span>{{ matchCountLabel }}</span>
        </div>

        <div class="result-list" v-if="isFileListResult">
          <div v-for="file in fileMatches" :key="file" class="result-item">
            <span class="path code">{{ file }}</span>
          </div>
        </div>

        <div class="result-list" v-else-if="lineMatches.length">
          <div
            v-for="match in lineMatches"
            :key="`${match.path}-${match.line}-${match.text}`"
            class="result-item"
          >
            <div class="result-meta">
              <span class="path code">{{ match.path }}</span>
              <span class="line-number" v-if="match.line">Line {{ match.line }}</span>
            </div>
            <pre class="matched-text">{{ match.text }}</pre>
          </div>
        </div>

        <div class="empty-state" v-else-if="isEmptyResult">未找到匹配结果</div>

        <div class="raw-result" v-else>
          <pre>{{ rawResultText }}</pre>
        </div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { parseToolCallArgs } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const parsedArgs = computed(() => parseToolCallArgs(props.toolCall))

const parsedResult = computed(() => {
  const content = props.toolCall.tool_call_result?.content
  if (!content) return null
  if (typeof content === 'object') return content
  try {
    return JSON.parse(content)
  } catch {
    return content
  }
})

const pattern = computed(() => parsedArgs.value.pattern || '')
const targetPath = computed(() => parsedArgs.value.path || parsedArgs.value.dir_path || '/')
const outputMode = computed(() => parsedArgs.value.output_mode || '')

const isFileListResult = computed(() => {
  return (
    Array.isArray(parsedResult.value) &&
    parsedResult.value.every((item) => typeof item === 'string')
  )
})

const fileMatches = computed(() => {
  return isFileListResult.value ? parsedResult.value : []
})

const lineMatches = computed(() => {
  if (!Array.isArray(parsedResult.value)) return []
  return parsedResult.value.filter(
    (item) =>
      item &&
      typeof item === 'object' &&
      typeof item.path === 'string' &&
      ('text' in item || 'line' in item)
  )
})

const isEmptyResult = computed(() => {
  return Array.isArray(parsedResult.value) && parsedResult.value.length === 0
})

const matchCountLabel = computed(() => {
  if (isFileListResult.value) {
    const count = fileMatches.value.length
    return count ? `共匹配 ${count} 个文件` : ''
  }
  if (lineMatches.value.length) {
    return `共匹配 ${lineMatches.value.length} 行`
  }
  return ''
})

const rawResultText = computed(() => {
  if (typeof parsedResult.value === 'string') return parsedResult.value
  return JSON.stringify(parsedResult.value, null, 2)
})
</script>

<style lang="less" scoped>
.grep-result {
  padding: 12px;
  background: var(--gray-0);

  .result-summary {
    font-size: 12px;
    color: var(--gray-600);
    margin-bottom: 10px;
  }

  .result-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .result-item {
    padding: 10px 12px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-25);
  }

  .result-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    min-width: 0;
  }

  .path {
    color: var(--gray-800);
    word-break: break-all;
  }

  .line-number {
    flex-shrink: 0;
    font-size: 12px;
    color: var(--main-700);
    background: var(--main-50);
    border-radius: 999px;
    padding: 2px 8px;
  }

  .matched-text,
  .raw-result pre {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 12px;
    line-height: 1.5;
    color: var(--gray-700);
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  }

  .matched-text {
    padding: 8px 10px;
    background: var(--gray-0);
    border-radius: 6px;
  }

  .empty-state {
    font-size: 12px;
    color: var(--gray-500);
  }
}
</style>
