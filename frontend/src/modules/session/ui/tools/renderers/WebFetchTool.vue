<template>
  <BaseToolCall :tool-call="toolCall" summary-only>
    <template #header>
      <div class="sep-header">
        <span class="note">网页抓取</span>
        <span v-if="label" class="separator">|</span>
        <span class="description" :title="url">{{ label }}</span>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { parseToolCallArgs, parseToolCallResult } from '../toolRegistry'

const props = defineProps({ toolCall: { type: Object, required: true } })
const resultData = computed(() => parseToolCallResult(props.toolCall))
const url = computed(() => resultData.value?.url || parseToolCallArgs(props.toolCall).url || '')
const label = computed(() => resultData.value?.title || url.value)
</script>
