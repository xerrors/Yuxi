<template>
  <a-dropdown
    v-model:open="open"
    :trigger="['click']"
    placement="topLeft"
    overlay-class-name="config-dropdown-overlay"
  >
    <button
      ref="triggerRef"
      type="button"
      class="input-action-btn config-dropdown-trigger"
      :class="{ 'is-trusted': modelValue === 'always_trust' }"
      :aria-label="currentOption.label"
      aria-haspopup="menu"
      :aria-expanded="open"
    >
      <component
        :is="currentOption.icon"
        :size="16"
        class="config-dropdown-compact-icon"
        aria-hidden="true"
      />
      <span class="hide-text config-dropdown-text">{{ currentOption.label }}</span>
      <ChevronDown :size="15" class="config-dropdown-chevron" />
    </button>

    <template #overlay>
      <div ref="panelRef" class="config-dropdown-panel" role="menu" aria-label="工具审批模式">
        <button
          v-for="option in options"
          :key="option.value"
          type="button"
          role="menuitemradio"
          :aria-checked="modelValue === option.value"
          class="config-dropdown-item"
          :class="{ selected: modelValue === option.value }"
          @click="selectMode(option.value)"
        >
          <component
            :is="option.icon"
            :size="15"
            class="config-dropdown-item-icon"
            :class="{ trusted: option.value === 'always_trust' }"
          />
          <span class="config-dropdown-item-label">{{ option.label }}</span>
          <Check v-if="modelValue === option.value" :size="14" class="config-dropdown-item-check" />
        </button>
      </div>
    </template>
  </a-dropdown>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Check, ChevronDown, Hand, ShieldAlert } from 'lucide-vue-next'
import { useOutsidePointerdown } from '@/composables/useOutsidePointerdown'

const props = defineProps({
  modelValue: { type: String, default: 'default' }
})

const emit = defineEmits(['update:modelValue'])
const options = [
  {
    value: 'default',
    label: '请求审批',
    icon: Hand
  },
  {
    value: 'always_trust',
    label: '完全信任',
    icon: ShieldAlert
  }
]

const open = ref(false)
const triggerRef = ref(null)
const panelRef = ref(null)
const currentOption = computed(
  () => options.find((option) => option.value === props.modelValue) || options[0]
)

const selectMode = (mode) => {
  emit('update:modelValue', mode)
  open.value = false
}

useOutsidePointerdown(open, [triggerRef, panelRef])
</script>

<style scoped lang="less">
.config-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: min(180px, calc(100vw - 160px));
  gap: 4px;

  &.is-trusted {
    color: var(--color-warning-700);
  }
}

.config-dropdown-trigger :deep(svg) {
  color: currentColor;
}

.config-dropdown-text {
  min-width: 0;
  overflow: hidden;
  color: currentColor;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.config-dropdown-chevron {
  flex-shrink: 0;
  color: currentColor;
}

.config-dropdown-compact-icon {
  display: none;
  flex-shrink: 0;
}

@container (max-width: 640px) {
  .config-dropdown-trigger {
    width: 30px;
    padding-inline: 0;
  }

  .config-dropdown-compact-icon {
    display: block;
  }

  .config-dropdown-text,
  .config-dropdown-chevron {
    display: none;
  }
}
</style>

<style lang="less">
.config-dropdown-overlay .config-dropdown-item-icon.trusted {
  color: var(--color-warning-700);
}
</style>
