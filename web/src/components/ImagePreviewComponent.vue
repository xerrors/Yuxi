<template>
  <div class="image-preview" v-if="imageData">
    <div class="image-container">
      <img
        :src="`data:${imageData.mimeType};base64,${imageData.imageContent}`"
        :alt="imageData.originalName"
        class="preview-image"
      />
      <button
        class="remove-button"
        type="button"
        :aria-label="`移除图片 ${imageData.originalName || ''}`"
        @click="handleRemove"
      >
        <X :size="14" />
      </button>
    </div>
  </div>
</template>

<script setup>
import { X } from 'lucide-vue-next'

defineProps({
  imageData: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['remove'])

// 移除图片
const handleRemove = () => {
  emit('remove')
}
</script>

<style lang="less" scoped>
.image-container {
  position: relative;
  display: inline-block;
  overflow: hidden;
}

.preview-image {
  width: 80px;
  height: 80px;
  object-fit: cover;
  display: block;
  border-radius: 8px;
  border: 1px solid var(--gray-200);
}

.remove-button {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  color: var(--gray-0);
  background: var(--gray-900);
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    transform 0.15s ease;

  &:hover {
    background: var(--gray-700);
  }

  &:active {
    transform: scale(0.96);
  }
}
</style>
