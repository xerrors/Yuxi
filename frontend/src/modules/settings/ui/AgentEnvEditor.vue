<template>
  <div class="env-editor-container">
    <div class="env-editor-toolbar">
      <div>
        <h3 class="settings-section-title">环境变量</h3>
      </div>
      <div class="env-editor-actions">
        <slot name="actions" />
        <a-button class="lucide-icon-btn" @click="addRow">
          <Plus :size="14" aria-hidden="true" />
          添加变量
        </a-button>
      </div>
    </div>
    <div class="env-card-list">
      <div v-for="(row, index) in rows" :key="index" class="env-card">
        <div class="env-key-cell">
          <span v-if="isKeyLocked(row)" class="env-key">{{ row.key }}</span>
          <a-input
            v-else
            v-model:value="row.key"
            placeholder="例如：API_KEY"
            aria-label="变量名"
            class="env-input font-mono"
          />
          <span
            v-if="keyVerification?.has(getRowKey(row))"
            class="env-key-result"
            :class="{ 'is-mismatched': !keyVerification.get(getRowKey(row)) }"
            role="img"
            :aria-label="keyVerification.get(getRowKey(row)) ? '验证通过' : '配置不一致'"
          >
            <Check v-if="keyVerification.get(getRowKey(row))" :size="12" aria-hidden="true" />
            <CircleAlert v-else :size="12" aria-hidden="true" />
            <span v-if="!keyVerification.get(getRowKey(row))">不一致</span>
          </span>
        </div>
        <div class="env-value-field">
          <a-input
            v-model:value="row.value"
            placeholder="变量值内容"
            :aria-label="`${row.key || '新变量'}的值`"
            class="env-input font-mono"
            :type="isValueHidden(row) ? 'password' : 'text'"
          />
          <a-button
            v-if="shouldConcealRow(row)"
            type="text"
            class="env-value-toggle lucide-icon-btn"
            :aria-label="isValueHidden(row) ? '查看变量值' : '隐藏变量值'"
            :aria-pressed="!isValueHidden(row)"
            @click="toggleValueVisible(row)"
          >
            <Eye v-if="isValueHidden(row)" :size="14" aria-hidden="true" />
            <EyeOff v-else :size="14" aria-hidden="true" />
          </a-button>
        </div>
        <a-tooltip title="删除变量">
          <a-button
            type="text"
            class="env-delete lucide-icon-btn"
            :aria-label="`删除变量 ${row.key || index + 1}`"
            @click="removeRow(index)"
          >
            <Trash2 :size="14" aria-hidden="true" />
          </a-button>
        </a-tooltip>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Check, CircleAlert, Eye, EyeOff, Plus, Trash2 } from '@lucide/vue'

const props = defineProps({
  modelValue: {
    type: Object,
    default: null
  },
  lockedKeys: {
    type: Array,
    default: () => []
  },
  concealLockedValues: {
    type: Boolean,
    default: false
  },
  keyVerification: {
    type: Map,
    default: null
  }
})

const emit = defineEmits(['update:modelValue'])

const rows = ref([{ key: '', value: '' }])
const syncingFromObject = ref(false)
const visibleValueKeys = ref(new Set())
const lockedKeySet = computed(() => new Set(props.lockedKeys.map((key) => String(key))))

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

const getRowKey = (row) => String(row?.key || '').trim()

const isKeyLocked = (row) => {
  const key = getRowKey(row)
  return Boolean(key && lockedKeySet.value.has(key))
}

const shouldConcealRow = (row) => props.concealLockedValues && isKeyLocked(row)

const isValueHidden = (row) => {
  const key = getRowKey(row)
  return shouldConcealRow(row) && !visibleValueKeys.value.has(key)
}

const toggleValueVisible = (row) => {
  const key = getRowKey(row)
  if (!key) return
  const next = new Set(visibleValueKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  visibleValueKeys.value = next
}

watch(
  () => props.lockedKeys,
  (keys) => {
    const nextLockedKeys = new Set(keys.map((key) => String(key)))
    visibleValueKeys.value = new Set(
      [...visibleValueKeys.value].filter((key) => nextLockedKeys.has(key))
    )
  }
)

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
.env-editor-container {
  min-width: 0;
  container: env-editor / inline-size;
}
.env-editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;

  .settings-section-title {
    margin: 0;
  }
}
.env-editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.env-card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.env-card {
  display: grid;
  grid-template-columns: minmax(160px, 0.85fr) minmax(120px, 1.15fr) 32px;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--gray-25);
  border: 1px solid var(--border-surface);
  border-radius: 8px;
}
.env-key-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.env-key,
.font-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
.env-key {
  color: var(--color-text);
  overflow-wrap: anywhere;
}
.env-input {
  min-width: 0;
}
.env-value-field {
  position: relative;
  min-width: 0;

  .env-input {
    padding-right: 40px;
  }
}
.env-value-toggle {
  position: absolute;
  right: 2px;
  top: 50%;
  transform: translateY(-50%);
  width: 28px;
  height: 28px;
  color: var(--color-text-secondary);
}
.env-delete {
  width: 32px;
  height: 32px;
  color: var(--color-text-secondary);

  &:hover {
    color: var(--color-error-700);
    background: var(--color-error-50);
  }
}
.env-key-result {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--color-success-900);
  font-size: 12px;

  flex-shrink: 0;

  &.is-mismatched {
    color: var(--color-warning-900);
    white-space: nowrap;
  }
}
@container env-editor (max-width: 440px) {
  .env-editor-toolbar {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .env-editor-actions {
    margin-left: auto;
  }
  .env-card {
    grid-template-columns: minmax(120px, 0.9fr) minmax(100px, 1.1fr) 28px;
    gap: 8px;
    padding: 12px;
  }
}
</style>
