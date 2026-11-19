<template>
  <div class="metadata-filter-group">
    <div class="filter-group-heading">
      <label :for="`${prefix}-group`" class="filter-control-label">分组匹配方式</label>
      <a-select
        :id="`${prefix}-group`"
        :value="modelValue.operator"
        :options="groupOptions"
        aria-label="分组匹配方式"
        class="group-mode"
        @update:value="update({ ...modelValue, operator: $event })"
      />
      <div class="filter-group-actions">
        <a-button
          size="small"
          :disabled="!fields.length || modelValue.conditions.length >= 50"
          @click="addCondition"
          >添加条件</a-button
        >
        <a-button
          v-if="depth < 4"
          size="small"
          :disabled="!fields.length || modelValue.conditions.length >= 50"
          @click="addGroup"
          >添加分组</a-button
        >
      </div>
    </div>
    <div v-for="(condition, index) in modelValue.conditions" :key="index" class="filter-condition">
      <MetadataFilterBuilder
        v-if="['and', 'or'].includes(condition.operator)"
        :model-value="condition"
        :fields="fields"
        :depth="depth + 1"
        @update:model-value="updateCondition(index, $event)"
      />
      <div v-else class="filter-condition-fields">
        <label :for="`${prefix}-${index}-field`" class="filter-control-label">筛选字段</label>
        <a-select
          :id="`${prefix}-${index}-field`"
          :value="condition.field || undefined"
          :options="fields.map((field) => ({ value: field.key, label: field.name }))"
          aria-label="筛选字段"
          placeholder="选择字段"
          @update:value="changeField(index, $event)"
        />
        <label :for="`${prefix}-${index}-operator`" class="filter-control-label">筛选关系</label>
        <a-select
          :id="`${prefix}-${index}-operator`"
          :value="condition.operator"
          :options="operatorOptions(condition.field)"
          aria-label="筛选关系"
          @update:value="updateCondition(index, { ...condition, operator: $event })"
        />
        <MetadataValueInput
          v-if="
            !['exists', 'not_exists'].includes(condition.operator) && findField(condition.field)
          "
          :field="findField(condition.field)"
          :model-value="condition.value"
          :aria-label="`${findField(condition.field).name}筛选值`"
          @update:model-value="updateCondition(index, { ...condition, value: $event })"
        />
      </div>
      <a-button
        type="text"
        danger
        :aria-label="`删除第${index + 1}项筛选`"
        class="filter-remove"
        @click="removeCondition(index)"
        ><X :size="16" aria-hidden="true"
      /></a-button>
    </div>
    <p v-if="!modelValue.conditions.length" class="filter-empty">
      请添加条件，或清空整个属性筛选。
    </p>
  </div>
</template>

<script setup>
import { useId } from 'vue'
import { X } from '@lucide/vue'
import MetadataValueInput from './MetadataValueInput.vue'
import { metadataOperatorNames } from '../model/metadata'
const props = defineProps({
  modelValue: { type: Object, required: true },
  fields: { type: Array, required: true },
  depth: { type: Number, default: 1 }
})
const emit = defineEmits(['update:modelValue'])
const prefix = useId()
const groupOptions = [
  { label: '满足全部条件（且）', value: 'and' },
  { label: '满足任一条件（或）', value: 'or' }
]
const findField = (key) => props.fields.find((field) => field.key === key)
const operatorOptions = (key) =>
  (findField(key)?.operators || []).map((value) => ({
    value,
    label: metadataOperatorNames[value] || value
  }))
const update = (value) => emit('update:modelValue', value)
const newCondition = () => ({
  field: props.fields[0]?.key || '',
  operator: props.fields[0]?.operators[0] || 'eq'
})
const addCondition = () =>
  update({ ...props.modelValue, conditions: [...props.modelValue.conditions, newCondition()] })
const addGroup = () =>
  update({
    ...props.modelValue,
    conditions: [...props.modelValue.conditions, { operator: 'or', conditions: [newCondition()] }]
  })
const removeCondition = (index) =>
  update({
    ...props.modelValue,
    conditions: props.modelValue.conditions.filter((_, current) => current !== index)
  })
const updateCondition = (index, value) =>
  update({
    ...props.modelValue,
    conditions: props.modelValue.conditions.map((item, current) =>
      current === index ? value : item
    )
  })
const changeField = (index, key) =>
  updateCondition(index, { field: key, operator: findField(key)?.operators[0] || 'eq' })
</script>

<style scoped>
.filter-control-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.metadata-filter-group {
  padding: 12px;
  background: var(--gray-10);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  min-width: 0;
}
.filter-group-heading,
.filter-group-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.filter-group-heading {
  justify-content: space-between;
}
.group-mode {
  min-width: 175px;
}
.filter-condition {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin-top: 10px;
  min-width: 0;
}
.filter-condition > .metadata-filter-group {
  flex: 1;
}
.filter-condition-fields {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(90px, 1fr) minmax(100px, 1fr) minmax(110px, 1.3fr);
  gap: 8px;
  min-width: 0;
}
.filter-remove {
  flex-shrink: 0;
}
.filter-empty {
  color: var(--color-text-secondary);
  margin: 12px 0 0;
}
@media (max-width: 900px) {
  .filter-condition-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
