<template>
  <span
    class="fallback-avatar"
    :class="[`fallback-avatar--${shape}`, { 'fallback-avatar--image': currentSrc }]"
    :style="[avatarSizeStyle, fallbackStyle]"
  >
    <img
      v-if="currentSrc"
      :key="currentSrc"
      class="fallback-avatar-image"
      :src="currentSrc"
      :alt="resolvedAlt"
      @error="handleImageError"
    />
    <span v-else class="fallback-avatar-text" aria-hidden="true">{{ initials }}</span>
  </span>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { getAvatarFallbackStyle, getAvatarInitials } from '@/utils/pixelAvatar'

const props = defineProps({
  src: {
    type: String,
    default: ''
  },
  defaultSrc: {
    type: String,
    default: ''
  },
  name: {
    type: String,
    default: ''
  },
  seed: {
    type: [String, Number],
    default: ''
  },
  kind: {
    type: String,
    default: 'user',
    validator: (value) => ['user', 'agent'].includes(value)
  },
  size: {
    type: [String, Number],
    default: 32
  },
  shape: {
    type: String,
    default: 'circle',
    validator: (value) => ['circle', 'rounded'].includes(value)
  },
  alt: {
    type: String,
    default: ''
  }
})

const failedImageCount = ref(0)

const imageCandidates = computed(() => {
  const candidates = [props.src, props.defaultSrc]
    .map((value) => String(value || '').trim())
    .filter(Boolean)
  return [...new Set(candidates)]
})

const currentSrc = computed(() => imageCandidates.value[failedImageCount.value] || '')

const initials = computed(() => getAvatarInitials(props.name, props.kind))

const fallbackStyle = computed(() => getAvatarFallbackStyle(props.seed || props.name || props.kind))

const avatarSizeStyle = computed(() => {
  const size = typeof props.size === 'number' ? `${props.size}px` : props.size
  const fontSize =
    typeof props.size === 'number' ? `${Math.max(10, Math.floor(props.size * 0.34))}px` : '12px'
  return {
    '--fallback-avatar-size': size,
    '--fallback-avatar-font-size': fontSize
  }
})

const resolvedAlt = computed(
  () => props.alt || props.name || (props.kind === 'agent' ? '智能体头像' : '用户头像')
)

const handleImageError = () => {
  if (failedImageCount.value < imageCandidates.value.length) {
    failedImageCount.value += 1
  }
}

watch(
  () => [props.src, props.defaultSrc],
  () => {
    failedImageCount.value = 0
  }
)
</script>

<style lang="less" scoped>
.fallback-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--fallback-avatar-size);
  height: var(--fallback-avatar-size);
  flex: 0 0 var(--fallback-avatar-size);
  overflow: hidden;
  border: 1px solid var(--gray-150);
  color: var(--gray-0);
  font-size: var(--fallback-avatar-font-size);
  font-weight: 600;
  line-height: 1;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.18);
  user-select: none;
}

.fallback-avatar--circle {
  border-radius: 50%;
}

.fallback-avatar--rounded {
  border-radius: 7px;
}

.fallback-avatar--image {
  background: var(--gray-0);
}

.fallback-avatar-image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.fallback-avatar-text {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-align: center;
  white-space: nowrap;
}
</style>
