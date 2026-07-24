<template>
  <a-modal
    :open="open"
    title="添加附件"
    ok-text="添加附件"
    cancel-text="取消"
    :confirm-loading="confirming"
    :ok-button-props="{ disabled: confirmDisabled }"
    @ok="handleConfirm"
    @cancel="handleCancel"
  >
    <a-upload-dragger
      :multiple="true"
      :show-upload-list="false"
      :before-upload="handleBeforeUpload"
      :disabled="confirming"
      class="attachment-dropzone"
    >
      <p class="dropzone-title">点击或拖拽文件到此处上传</p>
      <p class="dropzone-desc">支持任意文件格式 ≤ 5 MB；PDF 和图片可选解析为 Markdown。</p>
    </a-upload-dragger>

    <div v-if="fileItems.length" class="attachment-list">
      <div v-for="item in fileItems" :key="item.localId" class="attachment-item">
        <div class="attachment-file-icon">
          <FileTypeIcon :name="item.fileName" :size="20" />
        </div>

        <div class="attachment-item-content">
          <div class="attachment-name-row">
            <span class="attachment-name" :title="item.fileName">{{ item.fileName }}</span>
            <a-button
              size="small"
              type="text"
              class="lucide-icon-btn remove-btn"
              :disabled="confirming"
              @click="removeItem(item.localId)"
            >
              <X :size="16" />
            </a-button>
          </div>

          <div class="attachment-status-row">
            <div class="attachment-status-meta">
              <a-tag
                :color="getStatusColor(item.status)"
                :bordered="false"
                class="attachment-status-tag"
              >
                {{ getStatusLabel(item.status) }}
              </a-tag>
              <span>{{ formatFileSize(item.fileSize) }}</span>
              <span v-if="item.error" class="attachment-error">{{ item.error }}</span>
              <span v-else-if="item.parseError" class="attachment-error">{{
                item.parseError
              }}</span>
            </div>

            <div
              v-if="item.parseSupported && item.status !== 'uploading' && item.status !== 'error'"
              class="attachment-parse-controls"
            >
              <OCRSelector
                :model-value="item.selectedParseMethod"
                :allowed-engines="item.parseMethods"
                :disabled="item.status === 'parsing' || confirming"
                placeholder="选择 OCR"
                @update:model-value="handleParseMethodChange(item.localId, $event)"
              />
              <a-button
                type="primary"
                size="small"
                class="parse-trigger-btn"
                :loading="item.status === 'parsing'"
                :disabled="isParseDisabled(item)"
                @click="handleStartParse(item.localId)"
              >
                解析
              </a-button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { X } from 'lucide-vue-next'
import { threadApi } from '@/apis'
import { useConfigStore } from '@/stores/config'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import OCRSelector from '@/components/OCRSelector.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  threadId: { type: String, default: '' },
  ensureThread: { type: Function, default: null },
  initialFiles: { type: Array, default: () => [] },
  initialFilesKey: { type: Number, default: 0 }
})

const emit = defineEmits(['update:open', 'added'])

const DEFAULT_OCR_ENGINE = 'rapid_ocr'
const configStore = useConfigStore()
const fileItems = ref([])
const confirming = ref(false)
let localIdSeed = 0
let consumedInitialFilesKey = 0

const busy = computed(() =>
  fileItems.value.some((item) => ['uploading', 'parsing'].includes(item.status))
)
const confirmableItems = computed(() =>
  fileItems.value.filter((item) => ['uploaded', 'parsed'].includes(item.status))
)
const confirmDisabled = computed(() => busy.value || confirmableItems.value.length === 0)

watch(
  () => props.open,
  (open) => {
    if (!open) {
      fileItems.value = []
      confirming.value = false
    }
  }
)

const getErrorMessage = (error, fallback = '操作失败') => {
  return error?.response?.data?.detail || error?.message || fallback
}

const getDefaultParseMethod = (parseMethods) => {
  if (!Array.isArray(parseMethods) || parseMethods.length === 0) {
    return null
  }
  const configuredEngine = String(
    configStore.config?.default_ocr_engine || DEFAULT_OCR_ENGINE
  ).trim()
  if (configuredEngine === 'disable' && parseMethods.includes('disable')) {
    return configuredEngine
  }
  const selectableMethods = parseMethods.filter((method) => method !== 'disable')
  if (selectableMethods.includes(configuredEngine)) {
    return configuredEngine
  }
  if (selectableMethods.includes(DEFAULT_OCR_ENGINE)) {
    return DEFAULT_OCR_ENGINE
  }
  return selectableMethods[0] || null
}

const normalizeTmpUpload = (response) => ({
  tmpFileId: response.tmp_file_id,
  fileName: response.file_name,
  fileType: response.file_type,
  fileSize: response.file_size,
  bucketName: response.bucket_name,
  objectName: response.object_name,
  minioUrl: response.minio_url,
  parseSupported: response.parse_supported,
  parseMethods: response.parse_methods || [],
  selectedParseMethod: getDefaultParseMethod(response.parse_methods || []),
  parseMethodTouched: false
})

const updateItem = (localId, patch) => {
  fileItems.value = fileItems.value.map((item) =>
    item.localId === localId ? { ...item, ...patch } : item
  )
}

const uploadFile = async (file) => {
  const localId = `${Date.now()}-${localIdSeed++}`
  const item = {
    localId,
    fileName: file.name,
    fileSize: file.size,
    status: 'uploading',
    error: null,
    parseError: null,
    parseSupported: false,
    parseMethods: []
  }
  fileItems.value.push(item)

  try {
    const response = await threadApi.uploadTmpAttachment(file)
    const normalized = normalizeTmpUpload(response)
    updateItem(localId, { ...normalized, status: 'uploaded' })
  } catch (error) {
    updateItem(localId, {
      status: 'error',
      error: getErrorMessage(error, '上传失败')
    })
  }
}

const handleBeforeUpload = (file) => {
  void uploadFile(file)
  return false
}

const uploadInitialFiles = () => {
  if (!props.open || !props.initialFilesKey) return
  if (props.initialFilesKey === consumedInitialFilesKey) return

  consumedInitialFilesKey = props.initialFilesKey
  Array.from(props.initialFiles || [])
    .filter((file) => file instanceof File)
    .forEach((file) => {
      void uploadFile(file)
    })
}

watch(
  () => [props.open, props.initialFilesKey],
  () => {
    uploadInitialFiles()
  },
  { flush: 'post' }
)

const isParseDisabled = (item) =>
  item.status === 'parsing' || !item.selectedParseMethod || confirming.value

const clearParsedState = {
  parsedObjectName: null,
  parsedMinioUrl: null,
  truncated: false,
  parseMethod: null
}

const handleParseMethodChange = (localId, selectedParseMethod) => {
  const item = fileItems.value.find((entry) => entry.localId === localId)
  updateItem(localId, {
    ...clearParsedState,
    selectedParseMethod,
    parseMethodTouched: true,
    parseError: null,
    status: item?.status === 'parsed' ? 'uploaded' : item?.status
  })
}

const handleStartParse = (localId) => {
  const item = fileItems.value.find((entry) => entry.localId === localId)
  if (!item || isParseDisabled(item)) return
  void handleParse(item)
}

const handleParse = async (item) => {
  if (!item.objectName || !item.selectedParseMethod) return

  updateItem(item.localId, {
    ...clearParsedState,
    status: 'parsing',
    parseError: null
  })
  try {
    const response = await threadApi.parseTmpAttachment({
      object_name: item.objectName,
      file_name: item.fileName,
      bucket_name: item.bucketName,
      parse_method: item.selectedParseMethod
    })
    updateItem(item.localId, {
      status: 'parsed',
      parsedObjectName: response.parsed_object_name,
      parsedMinioUrl: response.parsed_minio_url,
      truncated: response.truncated,
      parseMethod: response.parse_method
    })
    message.success('附件解析完成')
  } catch (error) {
    updateItem(item.localId, {
      ...clearParsedState,
      status: 'uploaded',
      parseError: getErrorMessage(error, '解析失败')
    })
  }
}

const removeItem = (localId) => {
  fileItems.value = fileItems.value.filter((item) => item.localId !== localId)
}

const handleConfirm = async () => {
  if (confirmDisabled.value) return

  const attachments = confirmableItems.value.map((item) => ({
    file_name: item.fileName,
    file_type: item.fileType,
    bucket_name: item.bucketName,
    object_name: item.objectName,
    parsed_object_name: item.parsedObjectName || null,
    truncated: Boolean(item.truncated)
  }))

  confirming.value = true
  try {
    const threadId = props.threadId || (props.ensureThread ? await props.ensureThread() : '')
    if (!threadId) {
      message.error('创建对话失败，无法添加附件')
      return
    }

    const response = await threadApi.confirmTmpThreadAttachments(threadId, attachments)
    message.success('附件已添加')
    emit('added', response)
    emit('update:open', false)
  } catch (error) {
    message.error(getErrorMessage(error, '添加附件失败'))
  } finally {
    confirming.value = false
  }
}

const handleCancel = () => {
  emit('update:open', false)
}

const getStatusColor = (status) => {
  const colorMap = {
    uploading: 'processing',
    uploaded: 'blue',
    parsing: 'processing',
    parsed: 'green',
    error: 'red'
  }
  return colorMap[status] || 'default'
}

const getStatusLabel = (status) => {
  const labelMap = {
    uploading: '上传中',
    uploaded: '已上传',
    parsing: '解析中',
    parsed: '已解析',
    error: '失败'
  }
  return labelMap[status] || status
}

const formatFileSize = (size) => {
  if (!Number.isFinite(size)) return '未知大小'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
</script>

<style lang="less" scoped>
.attachment-dropzone {
  margin-bottom: 0;
}

.dropzone-title {
  margin: 0 0 4px;
  color: var(--gray-800);
  font-size: 14px;
  font-weight: 600;
}

.dropzone-desc {
  margin: 0;
  color: var(--gray-500);
  font-size: 12px;
}

.attachment-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 360px;
  margin-top: 16px;
  overflow: auto;
}

.attachment-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--gray-100);
  border-radius: 10px;
  background: var(--gray-50);
}

.attachment-file-icon {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--gray-0);
  font-size: 18px;
}

.attachment-item-content {
  min-width: 0;
  flex: 1;
}

.attachment-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.attachment-name {
  flex: 1;
  overflow: hidden;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  color: var(--gray-500);
}

.remove-btn:hover {
  color: var(--color-error-500);
  background: var(--color-error-50);
}

.attachment-status-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--gray-500);
  font-size: 11px;
}

.attachment-status-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.attachment-status-tag {
  min-height: 18px;
  margin-inline-end: 0;
  padding: 0 5px;
  border: none;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  line-height: 18px;
}

.attachment-error {
  color: var(--color-error-700);
}

.parse-trigger-btn {
  flex: none;
  font-size: 12px;
}

.attachment-parse-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  width: min(260px, 100%);
  margin-left: auto;

  :deep(.ocr-selector-trigger) {
    min-height: 24px;
    padding: 1px 8px;
    font-size: 12px;
  }
}
</style>
