<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">列出目录</span>
        <span class="separator" v-if="dirPath">|</span>
        <span class="description code">{{ dirPath }}</span>
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

const dirPath = computed(() => {
  return parsedArgs.value.dir_path || parsedArgs.value.path || ''
})
</script>

<style lang="less" scoped></style>
