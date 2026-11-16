<template>
  <div
    class="resource-empty-state"
    :class="{
      'resource-empty-state--compact': size === 'compact',
      'resource-empty-state--full-height': fullHeight
    }"
  >
    <div v-if="icon" class="resource-empty-state__icon" aria-hidden="true">
      <component :is="icon" :size="iconSize" :stroke-width="1.8" />
    </div>
    <h3 class="resource-empty-state__title">{{ title }}</h3>
    <p v-if="description" class="resource-empty-state__description">{{ description }}</p>
    <div v-if="$slots.actions" class="resource-empty-state__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Inbox } from '@lucide/vue'

const props = defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  icon: { type: [Object, Function, String], default: () => Inbox },
  size: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'compact'].includes(value)
  },
  fullHeight: { type: Boolean, default: false }
})

const iconSize = computed(() => (props.size === 'compact' ? 22 : 24))
</script>

<style scoped lang="less">
.resource-empty-state {
  display: flex;
  width: 100%;
  min-height: 300px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 72px 20px;
  text-align: center;

  &--compact {
    min-height: 240px;
    padding: 48px 16px;

    .resource-empty-state__icon {
      width: 42px;
      height: 42px;
      margin-bottom: 14px;
    }

    .resource-empty-state__title {
      font-size: 16px;
    }
  }

  &--full-height {
    height: 100%;
    min-height: 0;
    padding: 32px 20px;
  }

  &__icon {
    display: flex;
    width: 48px;
    height: 48px;
    align-items: center;
    justify-content: center;
    margin-bottom: 16px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--main-30);
    color: var(--main-color);
  }

  &__title {
    margin: 0;
    color: var(--gray-900);
    font-size: 18px;
    font-weight: 600;
    line-height: 1.35;
    letter-spacing: 0;
  }

  &__description {
    max-width: 360px;
    margin: 8px 0 0;
    color: var(--gray-600);
    font-size: 14px;
    line-height: 1.6;
  }

  &__actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
    margin-top: 20px;
  }
}

@media (max-width: 767px) {
  .resource-empty-state {
    min-height: 260px;
    padding: 56px 16px;

    &--compact {
      min-height: 220px;
      padding: 40px 16px;
    }

    &--full-height {
      min-height: 0;
      padding: 28px 16px;
    }

    &__description {
      max-width: 100%;
    }
  }
}
</style>
