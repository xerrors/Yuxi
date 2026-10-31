<template>
  <BaseToolCall :tool-call="toolCall" summary-only>
    <template #header>
      <div class="sep-header">
        <span class="note">{{ isSearch ? '搜索历史消息' : '读取历史消息' }}</span>
        <span v-if="label" class="separator">|</span>
        <span class="description" :title="label">{{ label }}</span>
        <span v-if="messages" class="tag">{{ messages.length }} 条</span>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { getToolCallId, parseToolCallArgs, parseToolCallResult } from '../toolRegistry'

const props = defineProps({ toolCall: { type: Object, required: true } })
const isSearch = computed(() => getToolCallId(props.toolCall) === 'search_thread_messages')
const result = computed(() => parseToolCallResult(props.toolCall))
const label = computed(() => isSearch.value ? parseToolCallArgs(props.toolCall).query : result.value?.title)
const messages = computed(() => {
  const items = isSearch.value ? result.value?.items : result.value?.messages
  return Array.isArray(items) ? items : null
})
</script>
