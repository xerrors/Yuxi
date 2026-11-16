<template>
  <BaseToolCall :tool-call="toolCall">
    <template #header>
      <div class="sep-header">
        <span class="note">编辑文件</span>
        <span class="separator" v-if="filePath">|</span>
        <span class="description" :title="filePath">
          <span class="code">{{ fileName }}</span>
          <span class="tag success" v-if="addedLines > 0">+{{ addedLines }}</span>
          <span class="tag error" v-if="removedLines > 0">-{{ removedLines }}</span>
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

const filePath = computed(() => parsedArgs.value.file_path || '')

// 仅显示文件名，悬浮时通过 title 显示完整路径
const fileName = computed(() => {
  const path = filePath.value
  if (!path) return ''
  return path.replace(/\\/g, '/').split('/').pop()
})

const addedLines = computed(() => {
  const newStr = parsedArgs.value.new_string || ''
  return newStr ? String(newStr).split('\n').length : 0
})

const removedLines = computed(() => {
  const oldStr = parsedArgs.value.old_string || ''
  return oldStr ? String(oldStr).split('\n').length : 0
})
</script>
