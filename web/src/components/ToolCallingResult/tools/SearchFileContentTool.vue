<template>
  <BaseToolCall :tool-call="toolCall">
    <template #header>
      <div class="sep-header">
        <span class="note">搜索文件内容</span>
        <span class="separator" v-if="pattern">|</span>
        <span class="note">{{ pattern }}</span>
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

const pattern = computed(() => parsedArgs.value.pattern || '')
const dirPath = computed(() => parsedArgs.value.dir_path || '')
</script>

<style lang="less" scoped></style>
