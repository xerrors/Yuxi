<template>
  <div class="agent-composer" :class="{ 'has-extra': showExtra && $slots.extra }">
    <div v-if="showExtra && $slots.extra" class="composer-extra-region" aria-label="对话上下文">
      <slot name="extra"></slot>
    </div>

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
      :file-upload-enabled="supportsFileUpload"
      :show-options-left="showInputOptions"
      @send="handleSend"
      @keydown="handleKeyDown"
      @paste-images="handlePastedImages"
      @drop-files="handleDroppedFiles"
    >
      <template #top>
        <div v-if="currentImages.length || previewAttachments.length" class="input-top-stack">
          <div v-if="currentImages.length" class="image-preview-list">
            <ImagePreviewComponent
              v-for="(image, index) in currentImages"
              :key="image.localId"
              :image-data="image"
              @remove="handleImageRemoved(index)"
              class="image-preview-wrapper"
            />
          </div>

          <div v-if="previewAttachments.length" class="attachment-preview-list">
            <div
              v-for="attachment in previewAttachments"
              :key="attachment.fileId"
              class="attachment-file-card"
            >
              <div class="attachment-file-icon">
                <FileTypeIcon :name="attachment.name" :size="18" />
              </div>
              <div class="attachment-file-body">
                <div class="attachment-file-name" :title="attachment.name">
                  {{ attachment.name }}
                </div>
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
          :disabled="disabled"
          :file-upload-enabled="supportsFileUpload"
          :mention="mention"
          @upload="handleAttachmentUpload"
          @upload-image-files="handleImageFilesSelected"
          @select-mention="handleMentionSelect"
        />
      </template>
      <template #actions-left>
        <div class="input-actions-left">
          <slot name="actions-left-extra"></slot>
        </div>
      </template>
      <template #actions-right>
        <div class="input-actions-right">
          <slot name="actions-right-extra"></slot>
        </div>
      </template>
    </MessageInputComponent>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import MessageInputComponent from '@/components/MessageInputComponent.vue'
import ImagePreviewComponent from '@/components/ImagePreviewComponent.vue'
import AttachmentOptionsComponent from '@/components/AttachmentOptionsComponent.vue'
import { X } from '@lucide/vue'
import { normalizeAttachmentPreviews } from '@/utils/file_utils'
import { uploadMultimodalImage } from '@/utils/multimodal_image_upload'
import {
  MAX_MULTIMODAL_IMAGES,
  MAX_MULTIMODAL_TOTAL_BASE64_BYTES,
  isWithinBase64Budget,
  remainingImageSlots,
  splitDroppedFiles
} from '@/utils/multimodal_image_limits'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  isLoading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  sendButtonDisabled: { type: Boolean, default: false },
  mention: { type: Object, default: () => null },
  threadId: { type: String, default: '' },
  showExtra: { type: Boolean, default: false },
  supportsFileUpload: { type: Boolean, default: false },
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
// 已上传成功的图片（每项就是 uploadMultimodalImage 的返回值 + localId）。
// 只放成功项：上传中与失败由 message 提示承担，避免列表里出现不可用的空卡片。
const currentImages = ref([])
let localIdSeed = 0
const nextLocalId = () => `image-${(localIdSeed += 1)}`
const placeholder = '问点什么？使用 @ 可以选择文件、知识库或技能进行引用。'

const previewAttachments = computed(() => normalizeAttachmentPreviews(props.attachments))
const showInputOptions = computed(
  () =>
    props.supportsFileUpload ||
    Boolean(props.mention?.knowledgeBases?.length) ||
    Boolean(props.mention?.skills?.length)
)

const updateValue = (val) => {
  emit('update:modelValue', val)
}

const handleAttachmentUpload = (files = []) => {
  emit('upload-attachment', files)
}

/** 并行上传一批图片文件，成功的逐张进列表。 */
const uploadImageFiles = async (files = []) => {
  const { images } = splitDroppedFiles(files)
  if (!images.length) return

  const accepted = images.slice(0, remainingImageSlots(currentImages.value))
  if (accepted.length < images.length) {
    message.error(`最多添加 ${MAX_MULTIMODAL_IMAGES} 张图片，超出的未添加`)
  }

  await Promise.all(
    accepted.map(async (file) => {
      const localId = nextLocalId()
      // 失败按张分 key：多张同时失败时每条都看得见，而不是只剩最后一条
      const imageData = await uploadMultimodalImage(file, `image-upload-${localId}`)
      if (imageData?.success) {
        currentImages.value.push({ ...imageData, localId })
      }
    })
  )
}

/** 菜单选图：关掉选项面板后与粘贴/拖拽同路。 */
const handleImageFilesSelected = (files = []) => {
  inputRef.value?.closeOptions()
  uploadImageFiles(files)
}

/** 粘贴：载荷是剪贴板里的全部图片（原先只取第一张）。 */
const handlePastedImages = async (files = []) => {
  if (props.disabled || !props.supportsFileUpload) return
  await uploadImageFiles(files)
}

/** 拖拽分流：图片走多模态直读；其它文件仍走附件通道（图片的 OCR 入口在「添加附件」菜单）。 */
const handleDroppedFiles = (files = []) => {
  if (props.disabled || !props.supportsFileUpload || !files.length) return
  const { images, others } = splitDroppedFiles(files)
  if (images.length) {
    uploadImageFiles(images)
  }
  if (others.length) {
    handleAttachmentUpload(others)
  }
}

const handleMentionSelect = (item) => {
  inputRef.value?.insertMention(item)
  inputRef.value?.closeOptions()
}

const handleImageRemoved = (index) => {
  currentImages.value.splice(index, 1)
}

// 发送被后端拒绝时把旧图片恢复到输入区，覆盖等待期间可能新选的图片，
// 避免旧图片被悄悄丢弃；用户可重新选择新图片。
const restoreImages = (images = []) => {
  currentImages.value = images.map((image) => ({ ...image, localId: nextLocalId() }))
}

const handleAttachmentRemoved = (attachment) => {
  emit('remove-attachment', attachment.raw)
}

const handleSend = () => {
  if (currentImages.value.length && !isWithinBase64Budget(currentImages.value)) {
    // 请求体是内联 base64，体积上限由网关与后端共同决定；超了就地拦下，不发请求。
    const limitMb = Math.round(MAX_MULTIMODAL_TOTAL_BASE64_BYTES / (1024 * 1024))
    message.error(`图片总大小超出单次请求上限（约 ${limitMb}MB），请减少张数或改用更小的图片`)
    return
  }

  emit('send', { images: [...currentImages.value] })
  currentImages.value = []
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
  closeOptions: () => inputRef.value?.closeOptions(),
  restoreImages
})
</script>

<style lang="less" scoped>
@import '@/components/composerStyles.less';

.agent-composer {
  width: 100%;
}

.composer-extra-region {
  .composer-top-attachment();
  display: flex;
  min-height: 36px;
  align-items: flex-start;
  gap: 6px;
  overflow-x: auto;
  padding: 4px 14px 2px;
}

.agent-composer.has-extra :deep(.input-box) {
  z-index: 1;
}

.input-actions-left {
  display: flex;
  align-items: center;
  gap: 2px;
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
  margin-bottom: 8px;
}

.image-preview-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.attachment-preview-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
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
}
</style>
