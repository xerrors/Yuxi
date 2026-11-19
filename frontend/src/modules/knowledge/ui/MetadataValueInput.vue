<template>
  <label v-if="!id" :for="controlId" class="metadata-value-label">{{ label }}</label>
  <a-input-number
    v-if="field.type === 'number'"
    :id="controlId"
    :value="modelValue"
    :disabled="disabled"
    :aria-label="label"
    style="width: 100%"
    @update:value="update"
  />
  <a-select
    v-else-if="field.type === 'boolean'"
    :id="controlId"
    :value="typeof modelValue === 'boolean' ? String(modelValue) : undefined"
    :disabled="disabled"
    :aria-label="label"
    :options="booleanOptions"
    placeholder="请选择"
    style="width: 100%"
    @update:value="update($event === 'true')"
  />
  <a-date-picker
    v-else-if="field.type === 'date'"
    :id="controlId"
    :value="modelValue"
    :disabled="disabled"
    :aria-label="label"
    value-format="YYYY-MM-DD"
    format="YYYY-MM-DD"
    style="width: 100%"
    @update:value="update"
  />
  <a-select
    v-else-if="['select', 'multi_select'].includes(field.type)"
    :id="controlId"
    :value="modelValue"
    :disabled="disabled"
    :aria-label="label"
    :mode="field.type === 'multi_select' ? 'multiple' : undefined"
    :options="field.options.map((value) => ({ label: value, value }))"
    placeholder="请选择"
    style="width: 100%"
    @update:value="update"
  />
  <a-textarea
    v-else
    :id="controlId"
    :value="modelValue"
    :disabled="disabled"
    :aria-label="label"
    :maxlength="4000"
    placeholder="请输入属性值"
    :auto-size="{ minRows: 1, maxRows: 4 }"
    @update:value="update"
  />
</template>

<script setup>
import { computed, useId } from 'vue'
const props = defineProps({
  field: { type: Object, required: true },
  modelValue: { type: [String, Number, Boolean, Array], default: undefined },
  disabled: { type: Boolean, default: false },
  id: { type: String, default: undefined },
  ariaLabel: { type: String, default: '' }
})
const emit = defineEmits(['update:modelValue'])
const generatedId = useId()
const controlId = computed(() => props.id || generatedId)
const label = computed(() => props.ariaLabel || props.field.name)
const booleanOptions = [
  { label: '是', value: 'true' },
  { label: '否', value: 'false' }
]
const update = (value) => emit('update:modelValue', value)
</script>

<style scoped>
.metadata-value-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
</style>
