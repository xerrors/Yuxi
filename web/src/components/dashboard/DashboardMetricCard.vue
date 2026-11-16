<template>
  <component
    :is="clickable ? 'button' : 'article'"
    :type="clickable ? 'button' : undefined"
    class="dashboard-metric-card"
    :class="[`tone-${tone}`, { compact }]"
    :aria-label="clickable ? ariaLabel || `${label} ${value}` : undefined"
    @click="clickable && $emit('click')"
  >
    <div class="dashboard-metric-content">
      <div v-if="icon" class="dashboard-metric-icon">
        <component :is="icon" class="dashboard-metric-icon-svg" aria-hidden="true" />
      </div>
      <div class="dashboard-metric-body">
        <div class="dashboard-metric-value">{{ value ?? 0 }}</div>
        <div class="dashboard-metric-label-row">
          <span class="dashboard-metric-label">{{ label }}</span>
          <span v-if="$slots.meta" class="dashboard-metric-meta"><slot name="meta" /></span>
        </div>
      </div>
    </div>
  </component>
</template>

<script setup>
defineProps({
  icon: {
    type: [Object, Function],
    default: null
  },
  value: {
    type: [String, Number],
    default: 0
  },
  label: {
    type: String,
    required: true
  },
  tone: {
    type: String,
    default: 'neutral',
    validator: (value) =>
      ['primary', 'success', 'info', 'warning', 'accent', 'neutral'].includes(value)
  },
  compact: {
    type: Boolean,
    default: false
  },
  clickable: {
    type: Boolean,
    default: false
  },
  ariaLabel: {
    type: String,
    default: ''
  }
})

defineEmits(['click'])
</script>

<style lang="less">
.dashboard-metric-card {
  --metric-color: var(--gray-700);
  --metric-surface: var(--gray-50);

  display: block;
  width: 100%;
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: inherit;
  text-align: left;
  transition:
    border-color 0.2s ease,
    background-color 0.2s ease;

  &.compact {
    padding: 10px;
  }

  &:hover {
    outline: none;
    background: var(--gray-10);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
  }

  &.tone-primary {
    --metric-color: var(--main-color);
    --metric-surface: var(--color-primary-50);
  }

  &.tone-success {
    --metric-color: var(--color-success-700);
    --metric-surface: var(--color-success-50);
  }

  &.tone-info {
    --metric-color: var(--color-info-700);
    --metric-surface: var(--color-info-50);
  }

  &.tone-warning {
    --metric-color: var(--color-warning-700);
    --metric-surface: var(--color-warning-50);
  }

  &.tone-accent {
    --metric-color: var(--color-accent-700);
    --metric-surface: var(--color-accent-50);
  }
}

.dashboard-metric-content {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 12px;
}

.dashboard-metric-icon {
  display: inline-flex;
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--metric-surface);
  color: var(--metric-color);
}

.dashboard-metric-icon-svg {
  width: 18px;
  height: 18px;
}

.dashboard-metric-body {
  min-width: 0;
  flex: 1;
}

.dashboard-metric-value {
  overflow: hidden;
  color: var(--gray-1000);
  font-size: 24px;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  line-height: 1.1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dashboard-metric-label-row {
  display: flex;
  min-width: 0;
  align-items: baseline;
  gap: 6px;
  margin-top: 5px;
}

.dashboard-metric-label,
.dashboard-metric-meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dashboard-metric-label {
  color: var(--gray-600);
  font-size: 12px;
  font-weight: 500;
}

.dashboard-metric-meta {
  color: var(--gray-500);
  font-size: 11px;
}

.compact .dashboard-metric-value {
  font-size: 18px;
}

.compact .dashboard-metric-label {
  font-size: 10px;
}

.compact .dashboard-metric-content {
  gap: 9px;
}

.compact .dashboard-metric-icon {
  width: 28px;
  height: 28px;
  flex-basis: 28px;
}

.compact .dashboard-metric-icon-svg {
  width: 15px;
  height: 15px;
}

.compact .dashboard-metric-label-row {
  margin-top: 2px;
}
</style>
