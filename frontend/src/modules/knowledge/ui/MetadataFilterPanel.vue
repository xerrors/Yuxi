<template>
  <section class="metadata-filter-panel" aria-label="文件属性筛选">
    <div class="filter-panel-heading">
      <span>文件属性筛选</span>
      <div>
        <a-button size="small" type="text" :loading="loading" @click="loadFields">刷新字段</a-button
        ><a-button
          v-if="modelValue"
          size="small"
          type="link"
          @click="emit('update:modelValue', null)"
          >清空筛选</a-button
        ><a-button v-else size="small" :disabled="loading || !fields.length" @click="start"
          >添加筛选</a-button
        >
      </div>
    </div>
    <a-alert v-if="error" type="error" :message="error" show-icon role="alert" />
    <MetadataFilterBuilder
      v-if="modelValue"
      :model-value="modelValue"
      :fields="fields"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <p v-else class="filter-panel-hint">
      {{
        loading
          ? '正在读取可用字段…'
          : fields.length
            ? '可按文件属性限定本次检索范围。'
            : '尚未定义属性字段，可在知识库配置中添加。'
      }}
    </p>
  </section>
</template>

<script setup>
import { ref, watch } from 'vue'
import { metadataApi } from '@/apis/knowledge_api'
import MetadataFilterBuilder from './MetadataFilterBuilder.vue'
const props = defineProps({
  refreshKey: { type: Number, default: 0 },
  kbId: { type: String, required: true },
  modelValue: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue', 'fields'])
const fields = ref([])
const loading = ref(false)
const error = ref('')
let request = 0
async function loadFields() {
  const current = ++request
  loading.value = true
  error.value = ''
  try {
    const result = await metadataApi.getFields(props.kbId)
    if (current !== request) return
    fields.value = result.fields
    emit('fields', result.fields)
  } catch (reason) {
    if (current === request) error.value = reason.message || '读取筛选字段失败'
  } finally {
    if (current === request) loading.value = false
  }
}
function start() {
  const first = fields.value[0]
  if (first)
    emit('update:modelValue', {
      operator: 'and',
      conditions: [{ field: first.key, operator: first.operators[0] }]
    })
}
watch(
  () => [props.kbId, props.refreshKey],
  ([kbId], previous) => {
    if (!previous || kbId !== previous[0]) emit('update:modelValue', null)
    fields.value = []
    loadFields()
  },
  { immediate: true }
)
</script>

<style scoped>
.metadata-filter-panel {
  margin: 12px 0;
}
.filter-panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.filter-panel-hint {
  color: var(--color-text-tertiary);
  margin: 0;
  font-size: 12px;
}
</style>
