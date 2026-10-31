<template>
  <BaseToolCall :tool-call="toolCall" summary-only>
    <template #header>
      <div class="sep-header"><span class="note">记录改进线索</span></div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import BaseToolCall from '../BaseToolCall.vue'

defineProps({ toolCall: { type: Object, required: true } })
</script>
