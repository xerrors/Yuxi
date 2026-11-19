<template>
  <div class="attribute-form">
    <p class="attribute-hint">
      {{
        newFiles
          ? '属性应用于本次添加的所有文件；只填写需要的字段，其他字段可以留空。'
          : '选择需要设置或清除的字段，其他属性保持原值。'
      }}
    </p>
    <div v-for="field in fields" :key="field.key" class="attribute-row">
      <label :for="`${prefix}-${field.key}`"
        >{{ field.name }}<span>{{ metadataTypeNames[field.type] }}</span></label
      >
      <div class="attribute-controls">
        <label :for="`${prefix}-${field.key}-operation`" class="attribute-operation-label"
          >{{ field.name }}修改方式</label
        >
        <a-select
          :id="`${prefix}-${field.key}-operation`"
          :value="operations[field.key] || 'keep'"
          :options="operationOptions"
          :aria-label="`${field.name}修改方式`"
          :disabled="disabled"
          class="attribute-operation"
          @update:value="changeOperation(field.key, $event)"
        />
        <MetadataValueInput
          :id="`${prefix}-${field.key}`"
          :field="field"
          :model-value="values[field.key]"
          :disabled="disabled || operations[field.key] !== 'set'"
          @update:model-value="changeValue(field.key, $event)"
        />
      </div>
      <p v-if="field.description" class="attribute-description">{{ field.description }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed, useId } from 'vue'
import MetadataValueInput from './MetadataValueInput.vue'
import { metadataTypeNames } from '../model/metadata'
const props = defineProps({
  fields: { type: Array, required: true },
  values: { type: Object, required: true },
  operations: { type: Object, required: true },
  newFiles: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['update:values', 'update:operations'])
const prefix = useId()
const operationOptions = computed(() =>
  props.newFiles
    ? [
        { label: '不填写', value: 'keep' },
        { label: '填写', value: 'set' }
      ]
    : [
        { label: '保留', value: 'keep' },
        { label: '设置', value: 'set' },
        { label: '清除', value: 'unset' }
      ]
)
const changeOperation = (key, value) =>
  emit('update:operations', { ...props.operations, [key]: value })
const changeValue = (key, value) => emit('update:values', { ...props.values, [key]: value })
</script>

<style scoped>
.attribute-hint,
.attribute-description {
  color: var(--color-text-secondary);
}
.attribute-row {
  margin: 16px 0;
}
.attribute-row label {
  display: block;
  margin-bottom: 6px;
  font-weight: 500;
}
.attribute-row label span {
  color: var(--color-text-tertiary);
  font-size: 12px;
  margin-left: 8px;
  font-weight: 400;
}
.attribute-controls {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 8px;
}
.attribute-operation-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.attribute-description {
  margin: 5px 0 0;
  font-size: 12px;
}
</style>
