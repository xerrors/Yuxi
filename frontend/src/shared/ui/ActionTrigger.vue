<template>
  <div class="action-trigger-shell" :class="{ 'is-field': variant === 'field' }">
    <button
      type="button"
      class="input-action-btn config-dropdown-trigger"
      :class="{ 'collapse-label': collapseLabel && $slots.icon, 'icon-only': iconOnly }"
      :disabled="disabled"
      :aria-label="label"
      :aria-expanded="open"
      :title="title || label"
    >
      <span v-if="$slots.icon" class="config-dropdown-compact-icon"><slot name="icon" /></span>
      <span v-if="!iconOnly" class="config-dropdown-text">{{ label }}</span>
      <ChevronDown v-if="!iconOnly" :size="15" class="config-dropdown-chevron" />
    </button>
    <span v-if="$slots.actions" class="action-trigger-actions"><slot name="actions" /></span>
  </div>
</template>

<script setup>
import { ChevronDown } from '@lucide/vue'
defineProps({
  label: { type: String, required: true },
  variant: {
    type: String,
    default: 'plain',
    validator: (value) => ['plain', 'field'].includes(value)
  },
  title: { type: String, default: '' },
  open: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  collapseLabel: { type: Boolean, default: false },
  iconOnly: { type: Boolean, default: false }
})
</script>

<style scoped lang="less">
.action-trigger-shell {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 100%;
  color: var(--gray-600);
}
.action-trigger-actions {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  padding-right: 6px;
}
.action-trigger-shell.is-field {
  width: 100%;
  height: 32px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-800);
  .config-dropdown-trigger {
    flex: 1;
    width: 100%;
    max-width: none;
    height: 30px;
    justify-content: flex-start;
  }
  .config-dropdown-text {
    flex: 1;
    text-align: left;
  }
}

.config-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: 240px;
  height: 30px;
  padding: 6px 8px;
  gap: 4px;
  border: none;
  border-radius: 8px;
  color: inherit;
  background: transparent;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.15s ease;

  &:hover:not(:disabled),
  &[aria-expanded='true'] {
    background: var(--gray-50);
  }
  &:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
  &:focus-visible {
    outline: 2px solid var(--main-400);
    outline-offset: 2px;
  }
}
.config-dropdown-text {
  min-width: 0;
  overflow: hidden;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.config-dropdown-compact-icon,
.config-dropdown-chevron {
  flex-shrink: 0;
}
.config-dropdown-compact-icon {
  display: inline-flex;
}
.collapse-label .config-dropdown-compact-icon {
  display: none;
}
.icon-only {
  width: 30px;
  padding-inline: 0;
}
@container (max-width: 640px) {
  .collapse-label {
    width: 30px;
    padding-inline: 0;
    .config-dropdown-compact-icon {
      display: inline-flex;
    }
    .config-dropdown-text,
    .config-dropdown-chevron {
      display: none;
    }
  }
}
</style>
