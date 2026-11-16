<template>
  <a-tooltip :title="tooltipText" placement="top" :mouse-enter-delay="0.15">
    <button
      type="button"
      class="context-usage-ring-btn"
      :class="[toneClass, { disabled }]"
      :disabled="disabled"
      aria-label="上下文使用情况"
      @click="handleClick"
    >
      <svg class="context-ring-svg" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
        <circle class="context-ring-bg" cx="8" cy="8" r="6" fill="none" stroke-width="2" />
        <circle
          v-if="computedRatio > 0"
          class="context-ring-fill"
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke-width="2"
          stroke-linecap="round"
          :stroke-dasharray="circumference"
          :stroke-dashoffset="dashOffset"
          transform="rotate(-90 8 8)"
        />
      </svg>
    </button>
  </a-tooltip>
</template>

<script setup>
import { computed } from 'vue'
import {
  calculateContextRatio,
  formatContextUsageTooltip,
  getContextUsageTone
} from '@/utils/contextUsage'

const props = defineProps({
  usedTokens: {
    type: Number,
    default: 0
  },
  limitTokens: {
    type: Number,
    default: null
  },
  ratio: {
    type: Number,
    default: null
  },
  disabled: {
    type: Boolean,
    default: false
  },
  title: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['click'])

const circumference = 2 * Math.PI * 6 // ~37.6991

const computedRatio = computed(() =>
  calculateContextRatio(props.usedTokens, props.limitTokens, props.ratio)
)

const dashOffset = computed(() => circumference * (1 - computedRatio.value))

const toneClass = computed(() => getContextUsageTone(computedRatio.value))

const tooltipText = computed(() =>
  formatContextUsageTooltip({
    usedTokens: props.usedTokens,
    limitTokens: props.limitTokens,
    ratio: props.ratio,
    customTitle: props.title
  })
)

const handleClick = (e) => {
  if (props.disabled) return
  emit('click', e)
}
</script>

<style lang="less" scoped>
.context-usage-ring-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  outline: none;
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease;
  user-select: none;

  &:hover:not(:disabled) {
    background: var(--gray-100);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
}

.context-ring-svg {
  display: block;

  .context-ring-bg {
    stroke: var(--gray-300);
    opacity: 0.65;
  }

  .context-ring-fill {
    stroke: var(--main-color);
    transition:
      stroke-dashoffset 0.3s ease,
      stroke 0.2s ease;
  }
}

.context-usage-ring-btn.is-warning {
  .context-ring-fill {
    stroke: var(--warning-color, #faad14);
  }
}

.context-usage-ring-btn.is-danger {
  .context-ring-fill {
    stroke: var(--error-color, #ff4d4f);
  }
}
</style>
