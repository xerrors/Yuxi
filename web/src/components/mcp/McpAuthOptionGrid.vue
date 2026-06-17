<template>
  <div class="auth-option-grid" :class="`is-${variant}`">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      class="auth-option-card"
      :class="{ active: model === option.value }"
      :disabled="readonly"
      @click="selectOption(option.value)"
    >
      <component :is="option.icon" :size="iconSize" />
      <span>{{ option.label }}</span>
      <small>{{ option.description }}</small>
    </button>
  </div>
</template>

<script setup>
const model = defineModel({ type: String, default: '' })

const props = defineProps({
  options: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
  variant: { type: String, default: 'default' },
  iconSize: { type: Number, default: 18 }
})

const emit = defineEmits(['select'])

const selectOption = (value) => {
  if (props.readonly) return
  model.value = value
  emit('select', value)
}
</script>

<style lang="less" scoped>
.auth-option-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 8px;
}

.auth-option-card {
  display: flex;
  min-height: 88px;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 5px;
  padding: 11px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-700);
  cursor: pointer;
  text-align: left;

  span {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 600;
  }

  small {
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.35;
  }

  &.active {
    border-color: var(--main-color);
    background: var(--main-10);
    color: var(--main-color);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.7;
  }
}

.is-compact .auth-option-card {
  min-height: 76px;
}
</style>
