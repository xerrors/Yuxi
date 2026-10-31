<template>
  <BaseToolCall :tool-call="toolCall" summary-only>
    <template #header>
      <div class="sep-header">
        <span class="note">查看智能体</span>
        <span v-if="agents" class="description">{{ agents.length }} 个可用</span>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { parseToolCallResult } from '../toolRegistry'

const props = defineProps({ toolCall: { type: Object, required: true } })
const agents = computed(() => {
  const result = parseToolCallResult(props.toolCall)
  return Array.isArray(result?.agents) ? result.agents : null
})
</script>
