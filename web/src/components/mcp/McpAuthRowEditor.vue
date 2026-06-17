<template>
  <div class="row-editor">
    <div
      v-for="(row, index) in rows"
      :key="`${rowKeyPrefix}-${index}`"
      class="row-editor-line"
      :class="{ 'is-readonly': readonly }"
    >
      <a-input
        :value="row[firstField]"
        :readonly="readonly"
        :placeholder="firstPlaceholder"
        @update:value="(value) => updateRow(index, firstField, value)"
      />
      <a-input
        :value="row[secondField]"
        :readonly="readonly"
        :placeholder="secondPlaceholder"
        @update:value="(value) => updateRow(index, secondField, value)"
      />
      <a-button
        v-if="!readonly"
        type="text"
        size="small"
        danger
        :disabled="rows.length === 1"
        @click="removeRow(index)"
      >
        <Trash2 :size="14" />
      </a-button>
    </div>
    <a-button v-if="!readonly" size="small" class="lucide-icon-btn" @click="addRow">
      <Plus :size="13" />
      <span>{{ addText }}</span>
    </a-button>
  </div>
</template>

<script setup>
import { Plus, Trash2 } from 'lucide-vue-next'

const rows = defineModel({ type: Array, required: true })

const props = defineProps({
  readonly: { type: Boolean, default: false },
  firstField: { type: String, default: 'key' },
  secondField: { type: String, default: 'value' },
  firstPlaceholder: { type: String, default: '名称' },
  secondPlaceholder: { type: String, default: '值' },
  addText: { type: String, default: '添加一行' },
  rowKeyPrefix: { type: String, default: 'row' }
})

const createEmptyRow = () => ({
  [props.firstField]: '',
  [props.secondField]: ''
})

const updateRow = (index, field, value) => {
  rows.value[index][field] = value
}

const addRow = () => {
  rows.value.push(createEmptyRow())
}

const removeRow = (index) => {
  if (rows.value.length === 1) {
    Object.assign(rows.value[0], createEmptyRow())
    return
  }
  rows.value.splice(index, 1)
}
</script>

<style lang="less" scoped>
.row-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.row-editor-line {
  display: grid;
  grid-template-columns: minmax(130px, 0.7fr) minmax(180px, 1.3fr) 36px;
  gap: 8px;
  align-items: center;

  &.is-readonly {
    grid-template-columns: minmax(130px, 0.7fr) minmax(180px, 1.3fr);
  }
}

@media (max-width: 720px) {
  .row-editor-line,
  .row-editor-line.is-readonly {
    grid-template-columns: 1fr;
  }
}
</style>
