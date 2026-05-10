<template>
  <div class="info-card" :class="{ 'info-card-disabled': disabled }" @click="$emit('click')">
    <div class="info-card-header">
      <div class="info-card-icon">
        <slot name="icon">
          <component :is="defaultIcon" v-if="defaultIcon" :size="20" />
        </slot>
      </div>
      <div class="info-card-info">
        <span class="info-card-name" :title="title">{{ title }}</span>
        <span v-if="subtitle" class="info-card-subtitle" :title="subtitle">{{ subtitle }}</span>
      </div>
      <div class="info-card-status">
        <slot name="status" />
        <template v-if="!$slots.status">
          <button
            v-if="actionLabel"
            type="button"
            class="card-action-btn"
            :class="`card-action-btn--${actionVariant || 'primary'}`"
            @click.stop="$emit('actionClick')"
          >
            {{ actionLabel }}
          </button>
          <template v-else-if="status">
            <span
              v-if="status.label"
              class="card-status-tag"
              :class="`card-status-tag--${status.level || 'info'}`"
              >{{ status.label }}</span
            >
            <span class="card-status-dot" :class="`card-status-dot--${statusDotColor}`"></span>
          </template>
        </template>
      </div>
    </div>

    <div v-if="$slots.info" class="info-card-body">
      <slot name="info" />
    </div>
    <div v-else-if="description" class="info-card-desc" :title="description">
      {{ description }}
    </div>
    <div v-else-if="info && info.length > 0" class="info-card-info-rows">
      <div v-for="(row, idx) in info" :key="idx" class="info-row">
        <span class="info-label">{{ row.label }}</span>
        <span class="info-value">{{ row.value }}</span>
      </div>
    </div>

    <div v-if="$slots.tags || (normalizedTags && normalizedTags.length > 0)" class="info-card-tags">
      <slot name="tags">
        <span
          v-for="(tag, idx) in normalizedTags"
          :key="idx"
          class="card-tag"
          :class="tag.color ? `tag-${tag.color}` : ''"
          :style="tag.bgColor ? { backgroundColor: tag.bgColor } : {}"
          >{{ tag.name }}</span
        >
      </slot>
    </div>

    <div v-if="$slots.footer" class="info-card-footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Plug } from 'lucide-vue-next'

const props = defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  description: { type: String, default: '' },
  info: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  defaultIcon: { type: [Object, String, Function], default: () => Plug },
  tags: { type: Array, default: () => [] },
  status: { type: Object, default: null },
  actionLabel: { type: String, default: '' },
  actionVariant: { type: String, default: 'primary' }
})

const statusDotColor = computed(() => {
  if (!props.status) return 'off'
  const level = props.status.level
  if (level === 'success') return 'on'
  if (level === 'warning' || level === 'error') return level
  return 'off'
})

defineEmits(['click', 'actionClick'])

const normalizedTags = computed(() => {
  if (!props.tags || props.tags.length === 0) return []
  return props.tags.map((t) => {
    if (typeof t === 'string') return { name: t }
    return { name: t.name || t.label || '', color: t.color, bgColor: t.bgColor }
  })
})
</script>

<style lang="less" scoped>
.info-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid var(--gray-150);
  background: linear-gradient(45deg, var(--gray-0) 0%, var(--gray-25) 100%);
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease;
  overflow: hidden;

  &:hover {
    border-color: var(--main-100);
    background: linear-gradient(45deg, var(--gray-0) 0%, var(--main-30) 100%);
  }

  &-disabled {
    opacity: 0.65;
    cursor: default;
  }

  &-header {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  &-icon {
    width: 40px;
    height: 40px;
    border-radius: 8px;
    background: var(--main-30);
    border: 1px solid var(--gray-150);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    color: var(--main-color);
    font-size: 18px;
    overflow: hidden;

    img {
      width: 24px;
      height: 24px;
      object-fit: contain;
    }
  }

  &-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  &-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--gray-900);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &-subtitle {
    font-size: 12px;
    color: var(--gray-600);
    font-family: monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &-status {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 4px;
    min-height: 24px;
  }

  &-desc {
    font-size: 13px;
    color: var(--gray-600);
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    min-height: 2.8em;
  }

  &-info-rows {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .info-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
  }

  .info-label {
    color: var(--gray-500);
    min-width: 72px;
    flex-shrink: 0;
  }

  .info-value {
    color: var(--gray-700);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &-tags {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  &-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin: 0 -16px -16px;
    padding: 10px 16px;
    border-top: 1px solid var(--gray-100);
    background: var(--gray-10);
  }
}

.card-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  min-width: 52px;
  min-height: 24px;
  padding: 0 9px;
  border: 1px solid transparent;
  box-shadow: none;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease;
  cursor: pointer;
  appearance: none;
  background: var(--main-50);
  color: var(--main-700);

  &:hover,
  &:focus {
    outline: none;
    border-color: var(--main-200);
    background: var(--main-50);
    color: var(--main-800);
  }

  &--danger {
    background: var(--color-error-50);
    color: var(--color-error-700);

    &:hover,
    &:focus {
      border-color: var(--color-error-200);
      background: var(--color-error-50);
      color: var(--color-error-800);
    }
  }
}

.card-status-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding: 0 9px;
  border-radius: 4px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  font-weight: 600;

  &--success,
  &--on {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &--warning {
    background: var(--color-warning-50);
    color: var(--color-warning-700);
  }

  &--error {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }

  &--info {
    background: var(--gray-100);
    color: var(--gray-600);
  }
}

.card-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-success-500);
  flex-shrink: 0;

  &--off {
    background: var(--gray-300);
  }

  &--warning {
    background: var(--color-warning-500);
  }

  &--error {
    background: var(--color-error-500);
  }

  &--on {
    background: var(--color-success-500);
  }
}
</style>
