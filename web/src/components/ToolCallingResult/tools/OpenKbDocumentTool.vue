<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">打开知识库文档</span>
        <span class="separator" v-if="resourceName">|</span>
        <span class="description" v-if="resourceName">知识库: {{ resourceName }}</span>
        <span class="separator" v-if="fileId">|</span>
        <span class="description" v-if="fileId">文件: {{ fileId }}</span>
        <span class="tag" v-if="lineLabel">{{ lineLabel }}</span>
      </div>
    </template>

    <template #result="{ resultContent }">
      <KbDocumentPreview
        v-if="isPreviewResult(parsedResult(resultContent))"
        :result="parsedResult(resultContent)"
        mode="open"
      />
      <div v-else class="plain-result">{{ stringifyResult(resultContent) }}</div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import KbDocumentPreview from './KbDocumentPreview.vue'
import { useDatabaseStore } from '@/stores/database'
import { parseToolCallArgs } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const databaseStore = useDatabaseStore()

const args = computed(() => parseToolCallArgs(props.toolCall))

const resourceName = computed(() => databaseStore.getDatabaseNameById(args.value.kb_id))
const fileId = computed(() => args.value.file_id || '')
const lineLabel = computed(() => {
  if (args.value.line) return `Line ${args.value.line}`
  if (args.value.offset !== undefined) return `Offset ${args.value.offset}`
  return ''
})

const parsedResult = (content) => {
  if (typeof content !== 'string') return content
  try {
    return JSON.parse(content)
  } catch {
    return content
  }
}

const isPreviewResult = (result) =>
  result && typeof result === 'object' && typeof result.content === 'string'

const stringifyResult = (content) => {
  const result = parsedResult(content)
  return typeof result === 'string' ? result : JSON.stringify(result, null, 2)
}
</script>

<style scoped lang="less">
.plain-result {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 10px 12px;
  color: var(--gray-700);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
