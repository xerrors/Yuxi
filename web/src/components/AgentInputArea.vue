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
    :thread-id="threadId"
    @send="handleSend"
    @keydown="handleKeyDown"
  >
    <template #top>
      <div v-if="currentImage || previewAttachments.length" class="input-top-stack">
        <ImagePreviewComponent
          v-if="currentImage"
          :image-data="currentImage"
          @remove="handleImageRemoved"
          class="image-preview-wrapper"
        />

        <div v-if="previewAttachments.length" class="attachment-preview-list">
          <div
            v-for="attachment in previewImageAttachments"
            :key="attachment.fileId"
            class="attachment-preview-image"
          >
            <img
              :src="attachment.previewUrl"
              :alt="attachment.name"
              class="attachment-image-thumb"
            />
            <button
              class="attachment-remove-btn"
              type="button"
              :aria-label="`移除附件 ${attachment.name}`"
              @click.stop="handleAttachmentRemoved(attachment)"
            >
              <X :size="14" />
            </button>
          </div>

          <div
            v-for="attachment in previewFileAttachments"
            :key="attachment.fileId"
            class="attachment-file-card"
          >
            <div class="attachment-file-icon" :style="{ color: attachment.iconColor }">
              <component :is="attachment.icon" />
            </div>
            <div class="attachment-file-body">
              <div class="attachment-file-name" :title="attachment.name">{{ attachment.name }}</div>
              <div class="attachment-file-meta">{{ attachment.meta }}</div>
            </div>
            <button
              class="attachment-remove-btn"
              type="button"
              :aria-label="`移除附件 ${attachment.name}`"
              @click.stop="handleAttachmentRemoved(attachment)"
            >
              <X :size="14" />
            </button>
          </div>
        </div>
      </div>
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
        <slot name="actions-left-extra"></slot>
        <a-popover
          v-if="showTodoEntry"
          v-model:open="todoPopoverOpen"
          placement="topLeft"
          trigger="click"
          overlay-class-name="todo-popover-overlay"
        >
          <template #content>
            <div class="todo-popover-card">
              <div class="todo-popover-header">
                <div class="todo-popover-title-wrap">
                  <span class="todo-popover-title">当前任务</span>
                  <span class="todo-popover-summary"
                    >{{ completedTodoCount }}/{{ totalTodoCount }} 已完成</span
                  >
                </div>
                <span class="todo-popover-progress">{{ todoProgress }}%</span>
              </div>

              <div class="todo-progress-bar">
                <span class="todo-progress-bar-fill" :style="{ width: `${todoProgress}%` }"></span>
              </div>

              <div class="todo-popover-list">
                <div
                  v-for="(todo, index) in todos"
                  :key="`${todo.content}-${index}`"
                  class="todo-item"
                >
                  <div class="todo-item-icon" :class="todo.status || 'unknown'">
                    <CheckCircleOutlined v-if="todo.status === 'completed'" />
                    <SyncOutlined v-else-if="todo.status === 'in_progress'" spin />
                    <ClockCircleOutlined v-else-if="todo.status === 'pending'" />
                    <CloseCircleOutlined v-else-if="todo.status === 'cancelled'" />
                    <QuestionCircleOutlined v-else />
                  </div>
                  <div class="todo-item-body">
                    <span class="todo-item-text">{{ todo.content }}</span>
                    <span class="todo-item-status">{{ getTodoStatusLabel(todo.status) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <button class="input-action-btn" @click.stop>
            <span class="todo-entry-icon" aria-hidden="true">
              <SquareCheck :size="16" />
            </span>
            <span>待办</span>
          </button>
        </a-popover>
      </div>
    </template>
    <template #actions-right>
      <div class="input-actions-right">
        <slot name="actions-right-extra"></slot>
      </div>
    </template>
  </MessageInputComponent>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import MessageInputComponent from '@/components/MessageInputComponent.vue'
import ImagePreviewComponent from '@/components/ImagePreviewComponent.vue'
import AttachmentOptionsComponent from '@/components/AttachmentOptionsComponent.vue'
import { SquareCheck, X } from 'lucide-vue-next'
import { normalizeAttachmentPreviews } from '@/utils/file_utils'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  QuestionCircleOutlined,
  SyncOutlined
} from '@ant-design/icons-vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  isLoading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  sendButtonDisabled: { type: Boolean, default: false },
  mention: { type: Object, default: () => null },
  threadId: { type: String, default: '' },
  supportsFileUpload: { type: Boolean, default: false },
  hasActiveThread: { type: Boolean, default: true },
  todos: {
    type: Array,
    default: () => []
  },
  attachments: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits([
  'update:modelValue',
  'send',
  'keydown',
  'upload-attachment',
  'remove-attachment'
])

const inputRef = ref(null)
const currentImage = ref(null)
const todoPopoverOpen = ref(false)
const placeholder = '问点什么？使用 @ 可以提及哦~'

const totalTodoCount = computed(() => props.todos.length)
const completedTodoCount = computed(
  () => props.todos.filter((todo) => todo?.status === 'completed').length
)
const showTodoEntry = computed(() => props.hasActiveThread && totalTodoCount.value > 0)
const todoProgress = computed(() => {
  if (!totalTodoCount.value) return 0
  return Math.round((completedTodoCount.value / totalTodoCount.value) * 100)
})
const previewAttachments = computed(() => normalizeAttachmentPreviews(props.attachments))
const previewImageAttachments = computed(() =>
  previewAttachments.value.filter((attachment) => attachment.isImage && attachment.previewUrl)
)
const previewFileAttachments = computed(() =>
  previewAttachments.value.filter((attachment) => !attachment.isImage || !attachment.previewUrl)
)

watch(showTodoEntry, (visible) => {
  if (!visible) {
    todoPopoverOpen.value = false
  }
})

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

const handleAttachmentRemoved = (attachment) => {
  emit('remove-attachment', attachment.raw)
}

const handleSend = () => {
  emit('send', { image: currentImage.value })
  currentImage.value = null
  todoPopoverOpen.value = false
}

const handleKeyDown = (e) => {
  if (props.sendButtonDisabled) {
    return
  }

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

const getTodoStatusLabel = (status) => {
  const labelMap = {
    completed: '已完成',
    in_progress: '进行中',
    pending: '待处理',
    cancelled: '已取消'
  }
  return labelMap[status] || '未知状态'
}
</script>

<style lang="less" scoped>
.input-actions-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.input-actions-right {
  display: flex;
  align-items: center;
  margin-right: 8px;
  gap: 2px;
}

.input-top-stack {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}

.attachment-preview-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.attachment-preview-image {
  position: relative;
  width: 80px;
  height: 80px;
  border-radius: 12px;
  border: 1px solid var(--gray-150);
  background: var(--gray-25);
  overflow: hidden;
}

.attachment-image-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.attachment-file-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  width: 220px;
  min-width: 0;
  padding: 10px 34px 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-0);
  box-shadow: 0 1px 4px var(--shadow-0);
}

.attachment-file-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--main-700);
  background: var(--main-30);
}

.attachment-file-body {
  min-width: 0;
}

.attachment-file-name {
  overflow: hidden;
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-file-meta {
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.3;
}

.attachment-remove-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
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

// 输入框操作按钮通用样式（穿透到 slot 内容）
:deep(.input-action-btn) {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  height: 30px;
  border-radius: 8px;
  font-size: 13px;
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

.todo-entry-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: currentColor;
}

.todo-popover-card {
  width: min(300px, calc(100vw - 32px));
  padding: 14px;
  background: linear-gradient(180deg, var(--gray-50) 0%, var(--gray-50) 100%);
}

.todo-popover-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.todo-popover-title-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.todo-popover-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--gray-900);
}

.todo-popover-summary {
  font-size: 12px;
  color: var(--gray-500);
}

.todo-popover-progress {
  font-size: 18px;
  line-height: 1;
  font-weight: 700;
  color: var(--gray-800);
}

.todo-progress-bar {
  position: relative;
  width: 100%;
  height: 6px;
  border-radius: 999px;
  background: var(--gray-100);
  overflow: hidden;
  margin-bottom: 12px;
}

.todo-progress-bar-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--color-success-500) 0%, var(--color-success-700) 100%);
}

.todo-popover-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 260px;
  overflow: auto;
  padding-right: 2px;
}

.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  padding: 6px 6px;
  border-radius: 6px;
  background: var(--light-70);
  box-shadow: inset 0 0 0 1px var(--light-70);
}

.todo-item-icon {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--gray-100);
  color: var(--gray-500);

  &.completed {
    background: var(--color-success-10);
    color: var(--color-success-700);
  }

  &.in_progress {
    background: var(--color-info-10);
    color: var(--color-info-700);
  }

  &.pending {
    background: var(--color-warning-10);
    color: var(--color-warning-700);
  }

  &.cancelled {
    background: var(--color-error-10);
    color: var(--color-error-700);
  }
}

.todo-item-body {
  min-width: 0;
}

.todo-item-text {
  font-size: 13px;
  line-height: 1.45;
  color: var(--gray-800);
  word-break: break-word;
  margin-right: 4px;
}

.todo-item-status {
  font-size: 12px;
  color: var(--gray-500);
}

// slot 内容的 hide-text 响应式样式
:deep(.hide-text) {
  @media (max-width: 768px) {
    display: none;
  }
}

@media (max-width: 768px) {
  .input-top-stack {
    gap: 8px;
    margin-bottom: 10px;
  }

  .attachment-file-card {
    width: min(220px, 100%);
  }

  .todo-popover-card {
    width: min(320px, calc(100vw - 24px));
    padding: 12px;
  }
}
</style>

<style lang="less">
.todo-popover-overlay {
  .ant-popover-inner {
    padding: 0;
    border-radius: 12px;
    overflow: hidden;
  }
}
</style>
