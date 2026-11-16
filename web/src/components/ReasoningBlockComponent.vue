<template>
  <BaseToolCall
    appearance="timeline"
    :status="isActive ? 'running' : 'completed'"
    hide-params
    force-show-result
  >
    <template #icon>
      <Brain :size="15" class="reasoning-icon" />
    </template>
    <template #header>
      <div class="sep-header">
        <span class="note">思考</span>
        <span class="separator">|</span>
        <span class="description">{{ preview }}</span>
      </div>
    </template>
    <template #result>
      <p class="reasoning-content">{{ content }}</p>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import { Brain } from '@lucide/vue'
import BaseToolCall from '@/components/ToolCallingResult/BaseToolCall.vue'

const props = defineProps({
  content: { type: String, required: true },
  isActive: { type: Boolean, default: false }
})

const preview = computed(() => props.content.replace(/\s+/g, ' ').trim())
</script>

<style lang="less" scoped>
.reasoning-icon {
  flex-shrink: 0;
  color: var(--gray-600);
}

.reasoning-content {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--gray-800);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
