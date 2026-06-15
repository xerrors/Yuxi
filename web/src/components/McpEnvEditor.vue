<template>
  <div class="env-editor">
    <div v-for="(row, index) in rows" :key="index" class="env-row">
      <a-input v-model:value="row.key" placeholder="Key" class="env-key-input" />
      <a-input v-model:value="row.value" placeholder="Value" class="env-value-input" />
      <a-button
        size="small"
        type="text"
        danger
        @click="removeRow(index)"
        :disabled="rows.length === 1"
      >
        删除
      </a-button>
    </div>
    <a-button @click="addRow" class="add-env">
      <template #icon><PlusOutlined /></template>
      添加变量
    </a-button>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { PlusOutlined } from '@ant-design/icons-vue'

const props = defineProps({
  modelValue: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:modelValue'])

const rows = ref([{ key: '', value: '' }])
const syncingFromObject = ref(false)

const objectToRows = (envObj) => {
  if (!envObj || typeof envObj !== 'object') {
    return [{ key: '', value: '' }]
  }
  const entries = Object.entries(envObj)
  if (entries.length === 0) {
    return [{ key: '', value: '' }]
  }
  return entries.map(([key, value]) => ({
    key,
    value: value == null ? '' : String(value)
  }))
}

const normalizeEnvObject = (value) => {
  if (value == null) {
    return null
  }
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed
      }
    } catch {
      return null
    }
    return null
  }
  if (typeof value === 'object' && !Array.isArray(value)) {
    return value
  }
  return null
}

const rowsToObject = (rowsValue) => {
  const entries = rowsValue
    .map((row) => ({
      key: row.key.trim(),
      value: row.value
    }))
    .filter((row) => row.key)
  if (entries.length === 0) {
    return null
  }
  return Object.fromEntries(entries.map((row) => [row.key, row.value]))
}

const addRow = () => {
  rows.value.push({ key: '', value: '' })
}

const removeRow = (index) => {
  if (rows.value.length === 1) {
    rows.value[0].key = ''
    rows.value[0].value = ''
    return
  }
  rows.value.splice(index, 1)
}

watch(
  () => props.modelValue,
  (value) => {
    const normalized = normalizeEnvObject(value)
    // 传入值若只是本组件 emit 出去的回声，则跳过重建 rows。否则 key 为空的行
    // （刚点击新增的空行、或正在输入 key 但 value 还为空的行）会被
    // rows -> object -> rows 的往返同步丢弃，导致无法新增环境变量。
    if (JSON.stringify(normalized) === JSON.stringify(rowsToObject(rows.value))) {
      return
    }
    syncingFromObject.value = true
    if (!normalized) {
      rows.value = [{ key: '', value: '' }]
    } else {
      rows.value = objectToRows(normalized)
    }
    syncingFromObject.value = false
  },
  { immediate: true }
)

watch(
  rows,
  (value) => {
    if (syncingFromObject.value) {
      return
    }
    const obj = rowsToObject(value)
    emit('update:modelValue', obj)
  },
  { deep: true }
)
</script>

<style lang="less" scoped>
.env-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;

  .env-row {
    display: flex;
    gap: 8px;
    align-items: center;

    .env-key-input,
    .env-value-input {
      flex: 1;
    }
  }

  button.add-env {
    width: fit-content;
  }
}
</style>
