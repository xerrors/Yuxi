<template>
  <div class="env-editor-container">
    <div class="settings-table-wrapper">
      <div class="env-table-header">
        <div class="col-key">变量名 (KEY)</div>
        <div class="col-value">变量值 (VALUE)</div>
        <div class="col-action">操作</div>
      </div>

      <div class="env-table-body">
        <div v-if="rows.length === 0" class="env-empty-row">
          <span>暂无环境变量，点击下方添加</span>
        </div>
        <div v-for="(row, index) in rows" :key="index" class="env-table-row">
          <div class="col-key">
            <a-input
              v-model:value="row.key"
              placeholder="例如：API_KEY"
              class="env-input font-mono"
              :disabled="isKeyLocked(row)"
            />
          </div>
          <div class="col-value">
            <div class="env-value-field">
              <a-input
                v-model:value="row.value"
                placeholder="变量值内容"
                class="env-input font-mono"
                :type="isValueHidden(row) ? 'password' : 'text'"
              />
              <a-button
                v-if="shouldConcealRow(row)"
                size="small"
                type="text"
                class="env-value-toggle"
                :aria-label="isValueHidden(row) ? '查看变量值' : '隐藏变量值'"
                @click="toggleValueVisible(row)"
              >
                <Eye v-if="isValueHidden(row)" :size="14" />
                <EyeOff v-else :size="14" />
              </a-button>
            </div>
          </div>
          <div class="col-action">
            <a-tooltip title="删除变量">
              <a-button
                type="text"
                size="small"
                danger
                class="action-btn lucide-icon-btn"
                @click="removeRow(index)"
              >
                <Trash2 :size="14" />
              </a-button>
            </a-tooltip>
          </div>
        </div>
      </div>
    </div>

    <div class="env-editor-footer">
      <a-button class="add-env-btn lucide-icon-btn" @click="addRow">
        <Plus :size="14" />
        <span>添加变量</span>
      </a-button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Eye, EyeOff, Plus, Trash2 } from '@lucide/vue'

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
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.settings-table-wrapper {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  overflow: hidden;
  background: var(--gray-0);

  .env-table-header {
    display: flex;
    align-items: center;
    background: var(--gray-50);
    border-bottom: 1px solid var(--gray-150);
    padding: 9px 14px;
    font-size: 12px;
    font-weight: 500;
    color: var(--gray-500);
    user-select: none;
  }

  .env-table-body {
    display: flex;
    flex-direction: column;
  }

  .env-table-row {
    display: flex;
    align-items: center;
    padding: 8px 14px;
    border-bottom: 1px solid var(--gray-100);
    transition: background 0.15s ease;

    &:last-child {
      border-bottom: none;
    }

    &:hover {
      background: var(--gray-25);
    }
  }

  .col-key {
    flex: 0 0 38%;
    padding-right: 12px;
  }

  .col-value {
    flex: 1;
    min-width: 0;
    padding-right: 12px;
  }

  .col-action {
    flex: 0 0 40px;
    display: flex;
    justify-content: center;
  }

  .env-input {
    border-radius: 6px;
    font-size: 12px;

    &.font-mono {
      font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
    }
  }

  .env-value-field {
    position: relative;
    display: flex;
    align-items: center;
    width: 100%;

    .env-input {
      width: 100%;
    }

    .env-value-toggle {
      position: absolute;
      right: 6px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      color: var(--gray-400);

      &:hover {
        color: var(--gray-700);
      }
    }
  }

  .action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    border-radius: 6px;
    color: var(--gray-400);
    transition: all 0.15s ease;

    &:hover:not(:disabled) {
      background: var(--color-error-50, #fff2f0);
      color: var(--color-error-500, #ff4d4f);
    }
  }

  .env-empty-row {
    padding: 36px 16px;
    text-align: center;
    color: var(--gray-400);
    font-size: 13px;
  }
}

.env-editor-footer {
  display: flex;
  align-items: center;

  .add-env-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--gray-700);
    border-radius: 6px;

    &:hover {
      color: var(--gray-900);
      border-color: var(--gray-300);
    }
  }
}
</style>
