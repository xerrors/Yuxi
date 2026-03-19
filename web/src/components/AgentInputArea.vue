<template>
  <MessageInputComponent
    ref="inputRef"
    :key="inputKey"
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
          v-if="hasStateContent"
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
import { ref, computed, watch } from 'vue'
import { message } from 'ant-design-vue'
import MessageInputComponent from '@/components/MessageInputComponent.vue'
import ImagePreviewComponent from '@/components/ImagePreviewComponent.vue'
import AttachmentOptionsComponent from '@/components/AttachmentOptionsComponent.vue'
import { threadApi } from '@/apis'
import { AgentValidator } from '@/utils/agentValidator'
import { handleChatError, handleValidationError } from '@/utils/errorHandler'
import { FolderCode } from 'lucide-vue-next'

const props = defineProps({
  modelValue: { type: String, default: '' },
  isLoading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  sendButtonDisabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '输入问题...' },
  supportsFileUpload: { type: Boolean, default: false },
  agentId: { type: String, default: '' },
  threadId: { type: String, default: null },
  ensureThread: { type: Function, required: true },
  hasStateContent: { type: Boolean, default: false },
  isPanelOpen: { type: Boolean, default: false },
  mention: { type: Object, default: () => null }
})

const emit = defineEmits([
  'update:modelValue',
  'send',
  'keydown',
  'attachment-changed',
  'toggle-panel'
])

const inputRef = ref(null)
const currentImage = ref(null)

// 用于强制重建输入组件的 key
const inputKey = ref(0)

// 监听 hasStateContent 变化，当从有 state 切换到无 state 时重建组件
watch(
  () => props.hasStateContent,
  (newVal, oldVal) => {
    // 当 hasStateContent 从 true 变为 false 时，重建输入组件
    if (oldVal === true && newVal === false) {
      inputKey.value++
    }
  }
)

const updateValue = (val) => {
  emit('update:modelValue', val)
}

const handleAttachmentUpload = async (files) => {
  if (!files?.length) return
  if (!AgentValidator.validateAgentIdWithError(props.agentId, '上传附件', handleValidationError))
    return

  const preferredTitle = files[0]?.name || '新的对话'
  let threadId = props.threadId

  if (!threadId) {
    try {
      threadId = await props.ensureThread(preferredTitle)
    } catch (e) {
      return
    }
  }

  if (!threadId) {
    message.error('创建对话失败，无法上传附件')
    return
  }

  try {
    const hide = message.loading({
      content: '正在上传附件...',
      key: 'upload-attachment',
      duration: 0
    })
    for (const file of files) {
      await threadApi.uploadThreadAttachment(threadId, file)
    }
    message.success({ content: '附件上传成功', key: 'upload-attachment', duration: 2 })
    emit('attachment-changed', threadId)
  } catch (error) {
    message.destroy('upload-attachment')
    handleChatError(error, 'upload')
  }
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
