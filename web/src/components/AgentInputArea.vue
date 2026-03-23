<template>
  <MessageInputComponent
    ref="inputRef"
    :model-value="modelValue"
    @update:modelValue="updateValue"
    :is-loading="isLoading"
    :disabled="disabled"
    :send-button-disabled="sendButtonDisabled"
    :placeholder="placeholder"
    :mention="mention"
    @send="handleSend"
    @keydown="handleKeyDown"
  >
    <template #top>
      <ImagePreviewComponent
        v-if="currentImage"
        :image-data="currentImage"
        @remove="handleImageRemoved"
        class="image-preview-wrapper"
      />
    </template>
    <template #options-left>
      <AttachmentOptionsComponent
        v-if="supportsFileUpload"
        :disabled="disabled"
        @upload="handleAttachmentUpload"
        @upload-image="handleImageUpload"
        @upload-image-success="handleImageUploadSuccess"
      />
    </template>
    <template #actions-left>
      <div class="input-actions-left">
        <!-- State Toggle Button -->
        <button
          v-if="hasActiveThread"
          class="input-action-btn"
          :class="{ active: isPanelOpen }"
          @click="$emit('toggle-panel')"
          title="查看工作状态"
        >
          <FolderCode :size="18" />
          <span>状态</span>
        </button>
      </div>
    </template>
    <template #actions-right>
      <div class="input-actions-right">
        <slot name="actions-left-extra"></slot>
      </div>
    </template>
  </MessageInputComponent>
</template>

<script setup>
import { ref } from 'vue'
import MessageInputComponent from '@/components/MessageInputComponent.vue'
import ImagePreviewComponent from '@/components/ImagePreviewComponent.vue'
import AttachmentOptionsComponent from '@/components/AttachmentOptionsComponent.vue'
import { FolderCode } from 'lucide-vue-next'

defineProps({
  modelValue: { type: String, default: '' },
  isLoading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  sendButtonDisabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '输入问题...' },
  mention: { type: Object, default: () => null },
  supportsFileUpload: { type: Boolean, default: false },
  isPanelOpen: { type: Boolean, default: false },
  hasActiveThread: { type: Boolean, default: true }
})

const emit = defineEmits([
  'update:modelValue',
  'send',
  'keydown',
  'upload-attachment',
  'toggle-panel'
])

const inputRef = ref(null)
const currentImage = ref(null)

const updateValue = (val) => {
  emit('update:modelValue', val)
}

const handleAttachmentUpload = (files) => {
  if (!files?.length) return
  emit('upload-attachment', files)
}

const handleImageUpload = (imageData) => {
  if (imageData && imageData.success) {
    currentImage.value = imageData
  }
}

const handleImageUploadSuccess = () => {
  if (inputRef.value) {
    inputRef.value.closeOptions()
  }
}

const handleImageRemoved = () => {
  currentImage.value = null
}

const handleSend = () => {
  emit('send', { image: currentImage.value })
  currentImage.value = null
}

const handleKeyDown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  } else {
    emit('keydown', e)
  }
}

defineExpose({
  focus: () => inputRef.value?.focus(),
  closeOptions: () => inputRef.value?.closeOptions()
})
</script>

<style lang="less" scoped>
.input-actions-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.input-actions-right {
  display: flex;
  align-items: center;
  margin-right: 8px;
}

// 输入框操作按钮通用样式（穿透到 slot 内容）
:deep(.input-action-btn) {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px;
  // height: 28px;
  border-radius: 8px;
  font-size: 14px;
  color: var(--gray-600);
  cursor: pointer;
  transition: all 0.2s ease;
  user-select: none;
  background: transparent;
  border: none;

  &:hover {
    color: var(--gray-900);
    background: var(--gray-50);
  }

  &.active {
    color: var(--gray-900);
    background: var(--gray-100);
    font-weight: 500;
  }

  &.disabled {
    opacity: 0.5;
    cursor: not-allowed;
    pointer-events: none;
  }

  span {
    line-height: 1;
  }
}

// slot 内容的 hide-text 响应式样式
:deep(.hide-text) {
  @media (max-width: 768px) {
    display: none;
  }
}
</style>
