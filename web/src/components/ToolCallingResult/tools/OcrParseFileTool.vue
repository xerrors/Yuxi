<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">OCR识别</span>
        <span class="separator" v-if="sourceName">|</span>
        <span class="description" :title="sourcePath">
          <span class="code">{{ sourceName }}</span>
          <span class="tag" v-if="resultData?.ocr_engine">{{ resultData.ocr_engine }}</span>
        </span>
      </div>
    </template>

    <template #result>
      <div class="ocr-result">
        <div class="result-row" v-if="resultData?.parsed_path">
          <span class="label">结果文件</span>
          <span class="value code">{{ resultData.parsed_path }}</span>
        </div>
        <div class="result-row" v-if="Number.isFinite(resultData?.char_count)">
          <span class="label">字符数</span>
          <span class="value">{{ resultData.char_count }}</span>
        </div>
        <pre v-if="resultData?.preview" class="preview">{{ resultData.preview }}</pre>
        <div v-if="resultData?.truncated" class="hint">预览已截断，完整内容请读取结果文件。</div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { parseToolCallArgs, parseToolCallResult } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const parsedArgs = computed(() => parseToolCallArgs(props.toolCall))
const resultData = computed(() => parseToolCallResult(props.toolCall) || {})
const sourcePath = computed(() => resultData.value.source_path || parsedArgs.value.file_path || '')
const sourceName = computed(() => {
  const path = sourcePath.value
  if (!path) return ''
  return path.replace(/\\/g, '/').split('/').pop()
})
</script>

<style lang="less" scoped>
.ocr-result {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.result-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
  font-size: 12px;
}

.label {
  flex: 0 0 auto;
  color: var(--gray-500);
}

.value {
  min-width: 0;
  color: var(--gray-800);
  overflow-wrap: anywhere;
}

.preview {
  max-height: 220px;
  margin: 0;
  padding: 8px;
  overflow: auto;
  color: var(--gray-800);
  background: var(--gray-50);
  border: 1px solid var(--gray-100);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.hint,
.tag {
  color: var(--gray-500);
}
</style>
