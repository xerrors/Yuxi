<template>
  <BaseToolCall :tool-call="toolCall" :command="command">
    <template #header>
      <div class="sep-header">
        <span v-if="command" class="description">{{ command }}</span>
        <span v-else class="note">执行命令</span>
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

const command = computed(() => typeof parsedArgs.value?.command === 'string' ? parsedArgs.value.command : '')
</script>
