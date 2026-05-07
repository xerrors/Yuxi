<template>
  <BaseToolCall :tool-call="toolCall">
    <template #header>
      <div class="sep-header">
        <span class="note">{{ subagentType }}</span>
        <span class="separator" v-if="shortDescription">|</span>
        <span class="description" v-if="shortDescription">{{ shortDescription }}</span>
      </div>
    </template>

    <template #params>
      <div v-if="description" class="task-description">{{ description }}</div>
    </template>

    <template #result="{ resultContent }">
      <div class="task-result">
        <MarkdownPreview compact :content="String(resultContent)" class="md-preview-wrapper" />
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const parsedArgs = computed(() => {
  const args = props.toolCall.args || props.toolCall.function?.arguments
  if (!args) return {}
  if (typeof args === 'object') return args
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
})

const subagentType = computed(() => parsedArgs.value.subagent_type || 'Unknown Agent')
const description = computed(() => parsedArgs.value.description || '')

const shortDescription = computed(() => {
  const desc = description.value
  if (!desc) return ''
  return desc.length > 50 ? desc.slice(0, 50) + '...' : desc
})
</script>

<style lang="less" scoped>
.sep-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  width: 100%;
  overflow: hidden;
}

.task-description {
  border-radius: 8px;
  font-size: 13px;
  color: var(--gray-800);
}

.task-result {
  padding: 12px;
  background: var(--gray-0);
  border-radius: 8px;

  .md-preview-wrapper {
    color: var(--gray-800);
  }
}
</style>
