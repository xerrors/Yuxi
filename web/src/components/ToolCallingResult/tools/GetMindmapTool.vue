<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">{{ operationLabel }}</span>
        <span class="separator" v-if="kbName">|</span>
        <span class="description" v-if="kbName">知识库: {{ kbName }}</span>
      </div>
    </template>
    <template #result="{ resultContent }">
      <div class="get-mindmap-result">
        <pre class="mindmap-content">{{ formatMindmapResult(resultContent) }}</pre>
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

const args = computed(() => parseToolCallArgs(props.toolCall))

const operationLabel = computed(() => '获取思维导图')

const kbName = computed(() => args.value.kb_name || '')

const formatMindmapResult = (content) => {
  if (typeof content === 'string') return content
  if (typeof content === 'object') return JSON.stringify(content, null, 2)
  return String(content)
}
</script>

<style scoped lang="less">
.get-mindmap-result {
  background: var(--gray-0);
  border-radius: 8px;
  padding: 12px 16px;
  max-height: 300px;
  overflow-y: auto;

  .mindmap-content {
    margin: 0;
    font-size: 13px;
    line-height: 1.6;
    color: var(--gray-700);
    white-space: pre-wrap;
    word-break: break-word;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  }
}
</style>
