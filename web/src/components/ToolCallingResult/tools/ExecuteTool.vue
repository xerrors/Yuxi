<template>
  <BaseToolCall :tool-call="toolCall">
    <template #header>
      <div class="sep-header">
        <span class="note">执行命令</span>
        <span class="separator" v-if="command">|</span>
        <span class="description" v-if="command">
          <span class="code">{{ command }}</span>
        </span>
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

const command = computed(() => {
  return parsedArgs.value.command || ''
})
</script>

<style lang="less" scoped></style>
