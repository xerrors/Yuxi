<template>
  <a-modal v-model:open="visible" title="添加文件" width="800px" @cancel="handleCancel">
    <template #footer>
      <div class="footer-container">
        <a-button type="link" class="help-link-btn" @click="openDocLink">
          <CircleHelp :size="14" /> 文档处理说明
        </a-button>
        <div class="footer-buttons">
          <a-button key="back" @click="handleCancel">取消</a-button>
          <a-button
            key="submit"
            type="primary"
            @click="chunkData"
            :loading="chunkLoading"
            :disabled="!canSubmit"
          >
            添加到知识库
          </a-button>
        </div>
      </div>
    </template>

    <div class="add-files-content">
      <!-- 1. 顶部操作栏 -->
      <div class="top-action-bar">
        <div class="mode-switch">
          <a-segmented
            v-model:value="uploadMode"
            :options="uploadModeOptions"
            class="custom-segmented"
          />
        </div>
        <div class="auto-index-toggle">
          <a-checkbox v-model:checked="autoIndex">上传后自动入库</a-checkbox>
        </div>
      </div>

      <!-- 2. 配置面板 -->
      <div
        class="settings-panel"
        v-if="folderTreeData.length > 0 || uploadMode !== 'url' || autoIndex"
      >
        <!-- 第一行：存储位置 + OCR 引擎 -->
        <div
          class="setting-row"
          v-if="folderTreeData.length > 0 || uploadMode !== 'url'"
          :class="{ 'two-cols': uploadMode !== 'url' && folderTreeData.length > 0 }"
        >
          <div class="col-item" v-if="folderTreeData.length > 0">
            <div class="setting-label">存储位置</div>
            <div class="setting-content flex-row">
              <a-tree-select
                v-model:value="selectedFolderId"
                show-search
                class="folder-select"
                :dropdown-style="{ maxHeight: '400px', overflow: 'auto' }"
                placeholder="选择目标文件夹（默认为根目录）"
                allow-clear
                tree-default-expand-all
                :tree-data="folderTreeData"
                tree-node-filter-prop="title"
              >
              </a-tree-select>
            </div>
            <p class="param-description">选择文件保存的目标文件夹</p>
          </div>
          <div class="col-item" v-if="uploadMode !== 'url'">
            <div class="setting-label">
              OCR 引擎（仅应用于 PDF/图片文件）
              <a-tooltip title="检查服务状态">
                <ReloadOutlined
                  class="action-icon refresh-icon"
                  :class="{ spinning: ocrHealthChecking }"
                  @click="checkOcrHealth"
                />
              </a-tooltip>
            </div>
            <div class="setting-content">
              <a-select
                v-model:value="chunkParams.enable_ocr"
                :options="enableOcrOptions"
                style="width: 100%"
                class="ocr-select"
                @dropdownVisibleChange="handleOcrDropdownVisibleChange"
              />
              <p class="param-description">
                <template v-if="!isOcrEnabled"> 不启用 OCR，仅处理文本文件 </template>
                <template v-else-if="selectedOcrStatus === 'healthy'">
                  {{ selectedOcrMessage || '服务正常' }}
                </template>
                <template v-else-if="selectedOcrStatus === 'unknown'">
                  点击刷新图标检查服务状态
                </template>
                <template v-else>
                  {{ selectedOcrMessage || '服务异常' }}
                </template>
              </p>
            </div>
          </div>
        </div>

        <!-- 第二行：自动入库配置 (仅在开启时显示) -->
        <div class="setting-row" v-if="autoIndex">
          <div class="col-item">
            <div class="setting-label">入库参数配置</div>
            <div class="setting-content">
              <ChunkParamsConfig
                :temp-chunk-params="indexParams"
                :show-qa-split="true"
                :show-chunk-size-overlap="!isGraphBased"
                :show-preset="true"
                :allow-preset-follow-default="true"
                :database-preset-id="
                  store.database?.additional_params?.chunk_preset_id || 'general'
                "
              />
              <p v-if="isGraphBased" class="param-description">
                LightRAG 按分隔符预切分，超长片段仍会按 token 大小继续切分。
              </p>
            </div>
          </div>
        </div>
      </div>

      <!-- PDF/图片OCR提醒 (Alert样式优化) -->
      <div v-if="hasPdfOrImageFiles && !isOcrEnabled" class="inline-alert warning">
        <Info :size="16" />
        <span>检测到PDF或图片文件，建议启用 OCR 以提取文本内容</span>
      </div>

      <!-- 文件上传区域 -->
      <div class="upload-area" v-if="uploadMode !== 'url'">
        <a-upload-dragger
          class="custom-dragger"
          v-model:fileList="fileList"
          name="file"
          :multiple="true"
          :directory="isFolderUpload"
          :disabled="chunkLoading"
          :show-upload-list="!showAggregateProgress"
          :accept="acceptedFileTypes"
          :before-upload="beforeUpload"
          :customRequest="customRequest"
          :action="'/api/knowledge/files/upload?db_id=' + databaseId"
          :headers="getAuthHeaders()"
          @change="handleFileUpload"
          @drop="handleDrop"
        >
          <p class="ant-upload-text">点击或将文件拖拽到此处</p>
          <p class="ant-upload-hint">支持类型: {{ uploadHint }}</p>
          <div class="zip-tip" v-if="hasZipFiles">📦 ZIP包将自动解压提取 Markdown 与图片</div>
        </a-upload-dragger>

        <div v-if="showAggregateProgress" class="upload-progress-card">
          <div class="progress-header">
            <div class="progress-header-left">
              <div class="progress-title">上传进度</div>
              <div class="progress-stats inline-in-header">
                <div class="stat-pill">总计 {{ totalUploadCount }}</div>
                <div class="stat-pill uploading" v-if="uploadingUploadCount > 0">
                  上传中 {{ uploadingUploadCount }}
                </div>
                <div class="stat-pill queued" v-if="queuedUploadCount > 0">
                  排队 {{ queuedUploadCount }}
                </div>
                <div class="stat-pill error" v-if="failedUploadCount > 0">
                  失败 {{ failedUploadCount }}
                </div>
              </div>
            </div>
            <div class="progress-header-right">
              <div class="progress-percent">{{ overallUploadProgress }}%</div>
              <a-button
                type="text"
                size="small"
                class="toggle-progress-btn"
                @click="progressExpanded = !progressExpanded"
              >
                <span>{{ progressExpanded ? '收起' : '展开' }}</span>
                <ChevronUp v-if="progressExpanded" :size="14" />
                <ChevronDown v-else :size="14" />
              </a-button>
            </div>
          </div>

          <div v-if="progressExpanded" class="progress-details">
            <div class="details-list" v-if="failedDetailItems.length > 0">
              <div v-for="item in failedDetailItems" :key="item.uid" class="detail-row">
                <span class="detail-name" :title="item.name">{{ item.name }}</span>
                <span class="detail-error" :title="item.errorText">{{ item.errorText }}</span>
              </div>
            </div>

            <div class="progress-tip" v-else>当前无失败文件。</div>

            <div class="progress-tip" v-if="hasPendingUploads">
              文件夹上传采用队列模式，最多同时上传 {{ MAX_UPLOAD_CONCURRENCY }} 个文件。
            </div>
            <div class="progress-tip" v-else>上传队列已完成，可点击“添加到知识库”继续下一步。</div>
          </div>
        </div>
      </div>

      <!-- URL 输入区域 -->
      <div class="url-area" v-if="uploadMode === 'url'">
        <div class="url-input-wrapper">
          <a-textarea
            v-model:value="newUrl"
            placeholder="输入 URL，一行一个&#10;https://site1.com&#10;https://site2.com"
            :auto-size="{ minRows: 4, maxRows: 8 }"
            class="url-input"
            @keydown.enter.ctrl="handleFetchUrls"
          />
          <div class="url-actions">
            <span class="url-hint">
              支持批量粘贴，自动过滤空行。
              <span class="warning-text">需配置白名单，详见文档说明</span>
            </span>
            <a-button
              type="primary"
              @click="handleFetchUrls"
              class="add-url-btn"
              :loading="fetchingUrls"
              :disabled="!newUrl.trim()"
            >
              加载 URLs
            </a-button>
          </div>
        </div>
        <div class="url-list" v-if="urlList.length > 0">
          <div v-for="(item, index) in urlList" :key="index" class="url-item">
            <div class="url-icon-wrapper">
              <Link v-if="item.status === 'success'" :size="14" class="url-icon success" />
              <Info
                v-else-if="item.status === 'error'"
                :size="14"
                class="url-icon error"
                :title="item.error"
              />
              <RotateCw v-else :size="14" class="url-icon spinning" />
            </div>
            <div class="url-content">
              <span class="url-text" :title="item.url">{{ item.url }}</span>
              <span v-if="item.status === 'error'" class="url-error-msg">{{ item.error }}</span>
            </div>
            <a-button type="text" size="small" class="remove-url-btn" @click="removeUrl(index)">
              <X :size="14" />
            </a-button>
          </div>
        </div>
        <div class="url-empty-tip" v-else>
          <Info :size="16" />
          <span>输入 URL 后点击加载，系统将自动抓取网页内容</span>
        </div>
      </div>

      <!-- 同名文件提示 -->
      <div v-if="sameNameFiles.length > 0" class="conflict-files-panel">
        <div class="panel-header">
          <Info :size="14" class="icon-warning" />
          <span>已存在同名文件 ({{ sameNameFiles.length }})</span>
        </div>
        <div class="file-list-scroll">
          <div v-for="file in sameNameFiles" :key="file.file_id" class="conflict-item">
            <div class="file-meta">
              <span class="fname" :title="file.filename">{{ file.filename }}</span>
              <span class="ftime">{{ formatFileTime(file.created_at) }}</span>
            </div>
            <div class="file-actions">
              <a-button
                type="text"
                size="small"
                class="action-btn download"
                @click="downloadSameNameFile(file)"
              >
                <Download :size="14" />
              </a-button>
              <a-button
                type="text"
                size="small"
                danger
                class="action-btn delete"
                @click="deleteSameNameFile(file)"
              >
                <Trash2 :size="14" />
              </a-button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { message, Upload, Modal } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'
import { useDatabaseStore } from '@/stores/database'
import { ocrApi } from '@/apis/system_api'
import { fileApi, documentApi } from '@/apis/knowledge_api'
import { ReloadOutlined } from '@ant-design/icons-vue'
import {
  FileUp,
  FolderUp,
  RotateCw,
  CircleHelp,
  Info,
  Download,
  Trash2,
  Link,
  X,
  ChevronDown,
  ChevronUp
} from 'lucide-vue-next'
import { h } from 'vue'
import ChunkParamsConfig from '@/components/ChunkParamsConfig.vue'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  folderTree: {
    type: Array,
    default: () => []
  },
  currentFolderId: {
    type: String,
    default: null
  },
  isFolderMode: {
    type: Boolean,
    default: false
  },
  mode: {
    type: String,
    default: 'file'
  }
})

const emit = defineEmits(['update:visible', 'success'])

const store = useDatabaseStore()

// 文件夹选择相关
const selectedFolderId = ref(null)
const folderTreeData = computed(() => {
  // 转换 folderTree 数据为 TreeSelect 需要的格式
  const transformData = (nodes) => {
    return nodes
      .map((node) => {
        if (!node.is_folder) return null
        return {
          title: node.filename,
          value: node.file_id,
          key: node.file_id,
          children: node.children ? transformData(node.children).filter(Boolean) : []
        }
      })
      .filter(Boolean)
  }
  return transformData(props.folderTree)
})

watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      selectedFolderId.value = props.currentFolderId
      isFolderUpload.value = props.isFolderMode
      uploadMode.value = props.mode || (props.isFolderMode ? 'folder' : 'file')
    }
  }
)

const DEFAULT_SUPPORTED_TYPES = ['.txt', '.pdf', '.jpg', '.jpeg', '.md', '.docx']

const normalizeExtensions = (extensions) => {
  if (!Array.isArray(extensions)) {
    return []
  }
  const normalized = extensions
    .map((ext) => (typeof ext === 'string' ? ext.trim().toLowerCase() : ''))
    .filter((ext) => ext.length > 0)
    .map((ext) => (ext.startsWith('.') ? ext : `.${ext}`))

  return Array.from(new Set(normalized)).sort()
}

const supportedFileTypes = ref(normalizeExtensions(DEFAULT_SUPPORTED_TYPES))

const applySupportedFileTypes = (extensions) => {
  const normalized = normalizeExtensions(extensions)
  if (normalized.length > 0) {
    supportedFileTypes.value = normalized
  } else {
    supportedFileTypes.value = normalizeExtensions(DEFAULT_SUPPORTED_TYPES)
  }
}

const acceptedFileTypes = computed(() => {
  if (!supportedFileTypes.value.length) {
    return ''
  }
  const exts = new Set(supportedFileTypes.value)
  exts.add('.zip')
  return Array.from(exts).join(',')
})

const uploadHint = computed(() => {
  if (!supportedFileTypes.value.length) {
    return '加载中...'
  }
  const exts = new Set(supportedFileTypes.value)
  exts.add('.zip')
  return Array.from(exts).join(', ')
})

const isSupportedExtension = (fileName) => {
  if (!fileName) {
    return true
  }
  if (!supportedFileTypes.value.length) {
    return true
  }
  const lastDotIndex = fileName.lastIndexOf('.')
  if (lastDotIndex === -1) {
    return false
  }
  const ext = fileName.slice(lastDotIndex).toLowerCase()
  return supportedFileTypes.value.includes(ext) || ext === '.zip'
}

const loadSupportedFileTypes = async () => {
  try {
    const data = await fileApi.getSupportedFileTypes()
    applySupportedFileTypes(data?.file_types)
  } catch (error) {
    console.error('获取支持的文件类型失败:', error)
    message.warning('获取支持的文件类型失败，已使用默认配置')
    applySupportedFileTypes(DEFAULT_SUPPORTED_TYPES)
  }
}

onMounted(() => {
  loadSupportedFileTypes()
})

const visible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value)
})

const databaseId = computed(() => store.databaseId)
const kbType = computed(() => store.database.kb_type)
const chunkLoading = computed(() => store.state.chunkLoading)

// 上传模式
const uploadMode = ref('file')
const MAX_UPLOAD_CONCURRENCY = 10

// 文件列表
const fileList = ref([])

const uploadQueue = ref([])
const activeUploadCount = ref(0)
const uploadTaskStatus = ref({})
const uploadTaskProgress = ref({})
const progressExpanded = ref(false)

const totalUploadCount = computed(() => fileList.value.length)
const queuedUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'queued').length
)
const uploadingUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'uploading').length
)
const successUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'done').length
)
const failedUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'error').length
)
const hasPendingUploads = computed(() => queuedUploadCount.value + uploadingUploadCount.value > 0)

const overallUploadProgress = computed(() => {
  const total = totalUploadCount.value
  if (!total) {
    return 0
  }
  const validUidSet = new Set(fileList.value.map((file) => file.uid).filter(Boolean))
  let sum = 0
  for (const uid of validUidSet) {
    sum += uploadTaskProgress.value[uid] || 0
  }
  return Math.round(sum / total)
})

const showAggregateProgress = computed(() => totalUploadCount.value >= MAX_UPLOAD_CONCURRENCY)

const failedDetailItems = computed(() => {
  return fileList.value
    .map((file) => {
      const uid = file.uid
      const rawStatus = uploadTaskStatus.value[uid] || file.status || 'unknown'
      const detail = file?.response?.detail || file?.error?.message || ''
      return {
        uid,
        name: file.name || '未命名文件',
        status: rawStatus,
        errorText: detail || '上传失败'
      }
    })
    .filter((item) => item.status === 'error')
})

const canSubmit = computed(() => {
  if (uploadMode.value === 'url') {
    return urlList.value.some((item) => item.status === 'success')
  }
  return successUploadCount.value > 0 && !hasPendingUploads.value
})

const uploadModeOptions = computed(() => [
  {
    value: 'file',
    label: h('div', { class: 'segmented-option' }, [
      h(FileUp, { size: 16, class: 'option-icon' }),
      h('span', { class: 'option-text' }, '上传文件')
    ])
  },
  {
    value: 'folder',
    label: h('div', { class: 'segmented-option' }, [
      h(FolderUp, { size: 16, class: 'option-icon' }),
      h('span', { class: 'option-text' }, '上传文件夹')
    ])
  },
  {
    value: 'url',
    label: h('div', { class: 'segmented-option' }, [
      h(Link, { size: 16, class: 'option-icon' }),
      h('span', { class: 'option-text' }, '解析 URL')
    ])
  }
])

watch(uploadMode, (val) => {
  isFolderUpload.value = val === 'folder'
  // 切换模式时清空已选内容，避免混淆
  fileList.value = []
  sameNameFiles.value = []
  urlList.value = []
  newUrl.value = ''
  for (const task of uploadQueue.value) {
    task.canceled = true
  }
  uploadQueue.value = []
  uploadTaskStatus.value = {}
  uploadTaskProgress.value = {}
  progressExpanded.value = false
})

watch(fileList, (newFileList) => {
  const validUidSet = new Set(newFileList.map((file) => file.uid).filter(Boolean))
  const nextStatus = {}
  const nextProgress = {}

  for (const [uid, status] of Object.entries(uploadTaskStatus.value)) {
    if (validUidSet.has(uid)) {
      nextStatus[uid] = status
    }
  }
  for (const [uid, progress] of Object.entries(uploadTaskProgress.value)) {
    if (validUidSet.has(uid)) {
      nextProgress[uid] = progress
    }
  }

  uploadTaskStatus.value = nextStatus
  uploadTaskProgress.value = nextProgress
})

// URL 列表
// Item structure: { url: string, status: 'fetching'|'success'|'error', data: object|null, error: string }
const urlList = ref([])
const newUrl = ref('')
const fetchingUrls = ref(false)
const CONTENT_EXISTS_ERROR_TEXT = '内容已存在于知识库中'

// 同名文件列表（用于显示提示）
const sameNameFiles = ref([])

// URL 相关功能
const isValidUrl = (string) => {
  try {
    const url = new URL(string)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

const mergeSameNameFiles = (sameNameList = []) => {
  if (!Array.isArray(sameNameList) || sameNameList.length === 0) {
    return
  }
  const existingIds = new Set(sameNameFiles.value.map((f) => f.file_id))
  const newConflicts = sameNameList.filter((f) => !existingIds.has(f.file_id))
  sameNameFiles.value.push(...newConflicts)
}

const fetchSingleUrlItem = async (item) => {
  item.status = 'fetching'
  try {
    const res = await fileApi.fetchUrl(item.url, databaseId.value)
    item.status = 'success'
    item.data = res
    mergeSameNameFiles(res.same_name_files)
  } catch (error) {
    console.error('Failed to fetch URL:', error)
    item.status = 'error'

    const detailData = error.response?.data?.detail
    const detailMessage =
      (typeof detailData === 'string' ? detailData : detailData?.message) || error.message || ''
    if (detailMessage.includes('same content') || detailMessage.includes('相同内容')) {
      item.error = CONTENT_EXISTS_ERROR_TEXT
      mergeSameNameFiles(detailData?.same_name_files)
    } else {
      item.error = detailMessage || '加载失败'
    }
  }
}

const handleFetchUrls = async () => {
  const text = newUrl.value
  if (!text) return

  const lines = text
    .split(/[\r\n]+/)
    .map((l) => l.trim())
    .filter((l) => l)
  if (lines.length === 0) return

  // 1. 预处理：添加到列表
  const newItems = []
  for (const url of lines) {
    if (!isValidUrl(url)) {
      continue
    }
    if (urlList.value.some((u) => u.url === url)) continue

    const item = { url, status: 'pending', data: null, error: '' }
    urlList.value.push(item)
    newItems.push(item)
  }

  if (newItems.length === 0) {
    if (lines.length > 0) {
      message.warning('没有检测到有效的新 URL')
    }
    return
  }

  newUrl.value = '' // 清空输入框
  fetchingUrls.value = true

  await Promise.all(newItems.map(fetchSingleUrlItem))
  fetchingUrls.value = false
}

const removeUrl = (index) => {
  urlList.value.splice(index, 1)
}

// OCR服务健康状态
const ocrHealthStatus = ref({
  rapid_ocr: { status: 'unknown', message: '' },
  mineru_ocr: { status: 'unknown', message: '' },
  mineru_official: { status: 'unknown', message: '' },
  pp_structure_v3_ocr: { status: 'unknown', message: '' },
  deepseek_ocr: { status: 'unknown', message: '' }
})

// OCR健康检查状态
const ocrHealthChecking = ref(false)

// 分块参数
const chunkParams = ref({
  enable_ocr: 'disable'
})

// 自动入库相关
const autoIndex = ref(false)
const indexParams = ref({
  chunk_size: 1000,
  chunk_overlap: 200,
  qa_separator: '',
  chunk_preset_id: ''
})

const buildAutoIndexParams = () => {
  const payload = {}
  if (indexParams.value.chunk_preset_id) {
    payload.chunk_preset_id = indexParams.value.chunk_preset_id
  }

  if (isGraphBased.value) {
    payload.qa_separator = indexParams.value.qa_separator || ''
    return payload
  }
  return {
    ...indexParams.value,
    ...payload
  }
}

const isGraphBased = computed(() => {
  const type = kbType.value?.toLowerCase()
  return type === 'lightrag'
})

const isFolderUpload = ref(false)

// 计算属性：是否启用了OCR
const isOcrEnabled = computed(() => {
  return chunkParams.value.enable_ocr !== 'disable'
})

// 上传模式切换相关逻辑已移除

// 计算属性：是否有PDF或图片文件
const hasPdfOrImageFiles = computed(() => {
  if (fileList.value.length === 0) {
    return false
  }

  const pdfExtensions = ['.pdf']
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.gif', '.webp']
  const ocrExtensions = [...pdfExtensions, ...imageExtensions]

  return fileList.value.some((file) => {
    if (file.status !== 'done') {
      return false
    }

    const filePath = file.response?.file_path || file.name
    if (!filePath) {
      return false
    }

    const ext = filePath.substring(filePath.lastIndexOf('.')).toLowerCase()
    return ocrExtensions.includes(ext)
  })
})

// 计算属性：是否有ZIP文件
const hasZipFiles = computed(() => {
  if (fileList.value.length === 0) {
    return false
  }

  return fileList.value.some((file) => {
    if (file.status !== 'done') {
      return false
    }

    const filePath = file.response?.file_path || file.name
    if (!filePath) {
      return false
    }

    const ext = filePath.substring(filePath.lastIndexOf('.')).toLowerCase()
    return ext === '.zip'
  })
})

// 计算属性：OCR选项
const enableOcrOptions = computed(() => [
  {
    value: 'disable',
    label: '不启用',
    title: '不启用'
  },
  {
    value: 'rapid_ocr',
    label: getRapidOcrLabel(),
    title: 'ONNX with RapidOCR',
    disabled:
      ocrHealthStatus.value?.rapid_ocr?.status === 'unavailable' ||
      ocrHealthStatus.value?.rapid_ocr?.status === 'error'
  },
  {
    value: 'mineru_ocr',
    label: getMinerULabel(),
    title: 'MinerU OCR',
    disabled:
      ocrHealthStatus.value?.mineru_ocr?.status === 'unavailable' ||
      ocrHealthStatus.value?.mineru_ocr?.status === 'error'
  },
  {
    value: 'mineru_official',
    label: getMinerUOfficialLabel(),
    title: 'MinerU Official API',
    disabled:
      ocrHealthStatus.value?.mineru_official?.status === 'unavailable' ||
      ocrHealthStatus.value?.mineru_official?.status === 'error'
  },
  {
    value: 'pp_structure_v3_ocr',
    label: getPPStructureV3Label(),
    title: 'PP-Structure-V3',
    disabled:
      ocrHealthStatus.value?.pp_structure_v3_ocr?.status === 'unavailable' ||
      ocrHealthStatus.value?.pp_structure_v3_ocr?.status === 'error'
  },
  {
    value: 'deepseek_ocr',
    label: getDeepSeekOcrLabel(),
    title: 'DeepSeek OCR (SiliconFlow)',
    disabled:
      ocrHealthStatus.value?.deepseek_ocr?.status === 'unavailable' ||
      ocrHealthStatus.value?.deepseek_ocr?.status === 'error'
  }
])

// 获取当前选中OCR服务的状态
const selectedOcrStatus = computed(() => {
  switch (chunkParams.value.enable_ocr) {
    case 'rapid_ocr':
      return ocrHealthStatus.value?.rapid_ocr?.status || 'unknown'
    case 'mineru_ocr':
      return ocrHealthStatus.value?.mineru_ocr?.status || 'unknown'
    case 'mineru_official':
      return ocrHealthStatus.value?.mineru_official?.status || 'unknown'
    case 'pp_structure_v3_ocr':
      return ocrHealthStatus.value?.pp_structure_v3_ocr?.status || 'unknown'
    case 'deepseek_ocr':
      return ocrHealthStatus.value?.deepseek_ocr?.status || 'unknown'
    default:
      return null
  }
})

// 获取当前选中OCR服务的状态消息
const selectedOcrMessage = computed(() => {
  switch (chunkParams.value.enable_ocr) {
    case 'rapid_ocr':
      return ocrHealthStatus.value?.rapid_ocr?.message || ''
    case 'mineru_ocr':
      return ocrHealthStatus.value?.mineru_ocr?.message || ''
    case 'mineru_official':
      return ocrHealthStatus.value?.mineru_official?.message || ''
    case 'pp_structure_v3_ocr':
      return ocrHealthStatus.value?.pp_structure_v3_ocr?.message || ''
    case 'deepseek_ocr':
      return ocrHealthStatus.value?.deepseek_ocr?.message || ''
    default:
      return ''
  }
})

// OCR服务状态图标映射
const STATUS_ICONS = {
  healthy: '✅',
  unavailable: '❌',
  unhealthy: '⚠️',
  timeout: '⏰',
  error: '⚠️',
  unknown: '❓'
}

// OCR选项标签生成通用函数
const getOcrLabel = (serviceKey, displayName) => {
  const status = ocrHealthStatus.value?.[serviceKey]?.status || 'unknown'
  return `${STATUS_ICONS[status] || '❓'} ${displayName}`
}

// 兼容性包装器
const getRapidOcrLabel = () => getOcrLabel('rapid_ocr', 'RapidOCR (ONNX)')
const getMinerULabel = () => getOcrLabel('mineru_ocr', 'MinerU OCR')
const getMinerUOfficialLabel = () => getOcrLabel('mineru_official', 'MinerU Official API')
const getPPStructureV3Label = () => getOcrLabel('pp_structure_v3_ocr', 'PP-Structure-V3')
const getDeepSeekOcrLabel = () => getOcrLabel('deepseek_ocr', 'DeepSeek OCR')

// 验证OCR服务可用性
const validateOcrService = () => {
  if (chunkParams.value.enable_ocr === 'disable') {
    return true
  }

  const status = selectedOcrStatus.value
  if (status === 'unavailable' || status === 'error') {
    const ocrMessage = selectedOcrMessage.value
    message.error(`OCR服务不可用: ${ocrMessage}`)
    return false
  }

  return true
}

const handleCancel = () => {
  emit('update:visible', false)
}

const beforeUpload = (file) => {
  if (!isSupportedExtension(file?.name)) {
    message.error(`不支持的文件类型：${file?.name || '未知文件'}`)
    return Upload.LIST_IGNORE
  }
  return true
}

const formatFileTime = (timestamp) => {
  if (!timestamp) return ''
  try {
    const date = new Date(timestamp)
    return date.toLocaleString()
  } catch {
    return timestamp
  }
}

const showSameNameFilesInUploadArea = (files) => {
  sameNameFiles.value = files
  // 可以在这里添加其他逻辑，比如自动滚动到提示区域
}

const downloadSameNameFile = async (file) => {
  try {
    // 获取当前数据库ID
    const currentDbId = databaseId.value
    if (!currentDbId) {
      message.error('知识库ID不存在')
      return
    }

    message.loading('正在下载文件...', 0)
    const response = await documentApi.downloadDocument(currentDbId, file.file_id)
    message.destroy()

    // 创建下载链接
    const blob = await response.blob() // 从 Response 对象中提取 Blob 数据
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)

    message.success(`文件 ${file.filename} 下载成功`)
  } catch (error) {
    message.destroy()
    console.error('下载文件失败:', error)
    message.error(`下载文件失败: ${error.message || '未知错误'}`)
  }
}

const deleteSameNameFile = (file) => {
  Modal.confirm({
    title: '确认删除文件',
    content: `确定要删除文件 "${file.filename}" 吗？此操作不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        // 获取当前数据库ID
        const currentDbId = databaseId.value
        if (!currentDbId) {
          message.error('知识库ID不存在')
          return
        }

        message.loading('正在删除文件...', 0)
        await documentApi.deleteDocument(currentDbId, file.file_id)
        message.destroy()

        // 从同名文件列表中移除
        sameNameFiles.value = sameNameFiles.value.filter((f) => f.file_id !== file.file_id)

        message.success(`文件 ${file.filename} 删除成功`)
      } catch (error) {
        message.destroy()
        console.error('删除文件失败:', error)
        message.error(`删除文件失败: ${error.message || '未知错误'}`)
      }
    }
  })
}

const customRequest = async (options) => {
  const fileUid = options.file?.uid
  if (fileUid) {
    uploadTaskStatus.value[fileUid] = 'queued'
    uploadTaskProgress.value[fileUid] = 0
  }

  const task = {
    options,
    xhr: null,
    canceled: false
  }

  uploadQueue.value.push(task)
  processUploadQueue()

  return {
    abort: () => {
      task.canceled = true
      if (task.xhr) {
        task.xhr.abort()
      }
      const queueIndex = uploadQueue.value.indexOf(task)
      if (queueIndex !== -1) {
        uploadQueue.value.splice(queueIndex, 1)
      }
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
    }
  }
}

const processUploadQueue = () => {
  while (activeUploadCount.value < MAX_UPLOAD_CONCURRENCY && uploadQueue.value.length > 0) {
    const task = uploadQueue.value.shift()
    if (!task || task.canceled) {
      continue
    }

    activeUploadCount.value += 1
    runUploadTask(task)
      .catch(() => {
        // 错误已经在 runUploadTask 内处理，这里只保证队列继续消费
      })
      .finally(() => {
        activeUploadCount.value -= 1
        processUploadQueue()
      })
  }
}

const runUploadTask = (task) => {
  const { file, onProgress, onSuccess, onError } = task.options
  const fileUid = file?.uid

  if (fileUid) {
    uploadTaskStatus.value[fileUid] = 'uploading'
  }

  return new Promise((resolve, reject) => {
    const formData = new FormData()
    const filename =
      isFolderUpload.value && file.webkitRelativePath ? file.webkitRelativePath : file.name
    formData.append('file', file, filename)

    const dbId = databaseId.value
    if (!dbId) {
      const error = new Error('Database ID is missing')
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      onError(error)
      reject(error)
      return
    }

    const xhr = new XMLHttpRequest()
    task.xhr = xhr
    xhr.open('POST', `/api/knowledge/files/upload?db_id=${dbId}`)

    const headers = getAuthHeaders()
    for (const [key, value] of Object.entries(headers)) {
      xhr.setRequestHeader(key, value)
    }

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        return
      }
      const percent = Math.min(100, (event.loaded / event.total) * 100)
      if (fileUid) {
        uploadTaskProgress.value[fileUid] = percent
      }
      onProgress({ percent })
    }

    xhr.onload = () => {
      if (task.canceled) {
        resolve()
        return
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const response = JSON.parse(xhr.responseText)
          if (fileUid) {
            uploadTaskStatus.value[fileUid] = 'done'
            uploadTaskProgress.value[fileUid] = 100
          }
          onSuccess(response, xhr)
          resolve()
        } catch (error) {
          if (fileUid) {
            uploadTaskStatus.value[fileUid] = 'error'
          }
          onError(error)
          reject(error)
        }
        return
      }

      let errorResp
      try {
        errorResp = JSON.parse(xhr.responseText || '{}')
      } catch {
        errorResp = {}
      }
      file.response = errorResp
      const error = new Error(errorResp.detail || 'Upload failed')
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      onError(error, file)
      reject(error)
    }

    xhr.onerror = (errorEvent) => {
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      onError(errorEvent)
      reject(errorEvent)
    }

    xhr.onabort = () => {
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      const abortError = new Error('Upload aborted')
      onError(abortError)
      reject(abortError)
    }

    xhr.send(formData)
  })
}

const handleFileUpload = (info) => {
  if (info?.file?.status === 'error') {
    const file = info.file
    // 尝试多种方式获取错误信息
    const detail = file?.response?.detail || file?.error?.message || ''
    if (detail.includes('same content') || detail.includes('相同内容')) {
      message.error(`${file.name} 已是相同内容文件，无需重复上传`)
    } else {
      message.error(detail || `文件上传失败：${file.name}`)
    }
  }

  // 检查是否有同名文件提示
  if (info?.file?.status === 'done' && info.file.response) {
    const response = info.file.response
    if (response.has_same_name && response.same_name_files && response.same_name_files.length > 0) {
      showSameNameFilesInUploadArea(response.same_name_files)
    }
  }

  fileList.value = info?.fileList ?? []
}

const handleDrop = () => {}

// 已移除文件夹上传逻辑

const checkOcrHealth = async () => {
  if (ocrHealthChecking.value) return

  ocrHealthChecking.value = true
  try {
    const healthData = await ocrApi.getHealth()
    ocrHealthStatus.value = healthData.services
  } catch (error) {
    console.error('OCR健康检查失败:', error)
    message.error('OCR服务健康检查失败')
  } finally {
    ocrHealthChecking.value = false
  }
}

const handleOcrDropdownVisibleChange = (open) => {
  if (!open) {
    return
  }
  checkOcrHealth()
}

const getAuthHeaders = () => {
  const userStore = useUserStore()
  return userStore.getAuthHeaders()
}

const openDocLink = () => {
  window.open(
    'https://xerrors.github.io/Yuxi/advanced/document-processing.html',
    '_blank',
    'noopener'
  )
}

const chunkData = async () => {
  if (!databaseId.value) {
    message.error('请先选择知识库')
    return
  }

  // 验证OCR服务可用性（非 URL 模式下）
  if (uploadMode.value !== 'url' && !validateOcrService()) {
    return
  }

  // URL 模式处理
  if (uploadMode.value === 'url') {
    // 过滤出成功的项
    const successfulItems = urlList.value.filter((item) => item.status === 'success' && item.data)
    if (successfulItems.length === 0) {
      message.error('请添加并等待至少一个 URL 解析成功')
      return
    }

    // 批内按内容哈希去重，避免同一批次重复入库
    const deduplicatedItems = []
    const seenKeys = new Set()
    let skippedDuplicates = 0
    for (const item of successfulItems) {
      const dedupKey = item.data?.content_hash || item.data?.file_path || item.url
      if (seenKeys.has(dedupKey)) {
        skippedDuplicates += 1
        continue
      }
      seenKeys.add(dedupKey)
      deduplicatedItems.push(item)
    }

    if (deduplicatedItems.length === 0) {
      message.error('URL 内容均为重复项，请更换后重试')
      return
    }

    if (skippedDuplicates > 0) {
      message.warning(`检测到 ${skippedDuplicates} 个重复 URL 内容，已保留首个并跳过其余项`)
    }

    try {
      store.state.chunkLoading = true
      const params = { ...chunkParams.value }
      if (autoIndex.value) {
        params.auto_index = true
        Object.assign(params, buildAutoIndexParams())
      }

      // 构造 _preprocessed_map 和 items (minio urls)
      const items = []
      const preprocessedMap = {}
      for (const item of deduplicatedItems) {
        // item.data = { file_path: "http://minio...", content_hash: "...", filename: "...", ... }
        // 注意：fetch-url 返回的 file_path 其实是 MinIO URL
        // 我们需要传递 MinIO URL 给 addDocuments
        const minioUrl = item.data.file_path
        items.push(minioUrl)
        preprocessedMap[minioUrl] = {
          path: minioUrl,
          content_hash: item.data.content_hash,
          filename: item.data.filename,
          file_size: item.data.size
        }
      }
      params._preprocessed_map = preprocessedMap

      // 调用 addFiles (file mode)
      await store.addFiles({
        items: items,
        contentType: 'file', // 重要：这里改为 file，因为我们已经转成了 minio 上的文件
        params,
        parentId: selectedFolderId.value
      })

      emit('success')
      handleCancel()
      urlList.value = []
      newUrl.value = ''
    } catch (error) {
      console.error('URL 提交失败:', error)
      message.error('URL 提交失败: ' + (error.message || '未知错误'))
    } finally {
      store.state.chunkLoading = false
    }
    return
  }

  // 文件模式处理
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif']

  // 提取已上传的文件信息
  const items = []
  const content_hashes = {}
  for (const file of fileList.value) {
    if (file.status !== 'done') continue
    const file_path = file.response?.file_path
    const content_hash = file.response?.content_hash
    if (!file_path) continue

    items.push(file_path)
    if (content_hash) content_hashes[file_path] = content_hash

    // 检查是否需要OCR
    const ext = file_path.substring(file_path.lastIndexOf('.')).toLowerCase()
    if (imageExtensions.includes(ext) && chunkParams.value.enable_ocr === 'disable') {
      message.error({
        content: '检测到图片文件，必须启用 OCR 才能提取文本内容。',
        duration: 5
      })
      return
    }
  }

  if (items.length === 0) {
    message.error('请先上传文件')
    return
  }

  try {
    store.state.chunkLoading = true
    const params = { ...chunkParams.value, content_hashes }
    if (autoIndex.value) {
      params.auto_index = true
      Object.assign(params, buildAutoIndexParams())
    }

    await store.addFiles({
      items,
      contentType: 'file',
      params,
      parentId: selectedFolderId.value
    })

    emit('success')
    handleCancel()
    fileList.value = []
    sameNameFiles.value = []
  } catch (error) {
    console.error('文件上传失败:', error)
    message.error('文件上传失败: ' + (error.message || '未知错误'))
  } finally {
    store.state.chunkLoading = false
  }
}
</script>

<style lang="less" scoped>
.footer-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.footer-buttons {
  display: flex;
  gap: 8px;
}

.add-files-content {
  padding: 8px 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Top Bar */
.top-action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.auto-index-toggle {
  display: flex;
  align-items: center;
  padding-right: 4px;

  :deep(.ant-checkbox-wrapper) {
    font-size: 13px;
    color: var(--gray-600);
    font-weight: 500;
  }
}

.help-link-btn {
  color: var(--gray-600);
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0;

  &:hover {
    color: var(--main-color);
  }
}

.custom-segmented {
  background-color: var(--gray-100);
  padding: 3px;

  .segmented-option {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 32px;
    .option-text {
      margin-left: 6px;
    }
  }
}

/* Settings Panel */
.settings-panel {
  background-color: var(--gray-50);
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.setting-row {
  display: flex;
  flex-direction: column;
  gap: 8px;

  &.two-cols {
    flex-direction: row;
    gap: 20px;
  }

  .col-item {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0; // Fix flex overflow
  }
}

.setting-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--gray-700);
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-icon {
  color: var(--gray-400);
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: var(--main-color);
  }

  &.spinning {
    animation: spin 1s linear infinite;
    color: var(--main-color);
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.flex-row {
  display: flex;
  align-items: center;
  width: 100%;
}

.folder-select {
  flex: 1;
}

.folder-checkbox {
  margin-left: 12px;
  white-space: nowrap;
}

.param-description {
  font-size: 12px;
  color: var(--gray-400);
  margin: 4px 0 0 0;
  line-height: 1.4;
  display: flex;
  align-items: center;
  gap: 4px;

  .text-success {
    color: var(--color-success-500);
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .text-warning {
    color: var(--color-warning-500);
    display: flex;
    align-items: center;
    gap: 4px;
  }
}

/* Chunk Display Card */
.chunk-display-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-300);
  border-radius: 6px;
  padding: 0 12px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: var(--main-color);
    box-shadow: 0 0 0 2px var(--main-100);

    .edit-icon {
      color: var(--main-color);
    }
  }

  &.disabled {
    background: var(--gray-100);
    cursor: not-allowed;
    color: var(--gray-400);
    &:hover {
      border-color: var(--gray-300);
      box-shadow: none;
    }
  }
}

.chunk-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--gray-700);

  .divider {
    color: var(--gray-300);
    font-size: 10px;
  }

  b {
    font-weight: 600;
    color: var(--gray-900);
  }
}

.edit-icon {
  color: var(--gray-400);
  font-size: 14px;
}

/* Alerts */
.inline-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;

  &.warning {
    background: var(--color-warning-50);
    border: 1px solid var(--color-warning-200);
    color: var(--color-warning-700);
  }
}

/* Upload Area */
.upload-area {
  flex: 1;
}

.custom-dragger {
  :deep(.ant-upload-drag) {
    background: var(--gray-0);
    border-radius: 8px;
    border: 1px dashed var(--gray-300);
    transition: all 0.3s;

    &:hover {
      border-color: var(--main-color);
      background: var(--main-50);
    }
  }

  .ant-upload-drag-icon {
    font-size: 32px;
    color: var(--main-300);
    margin-bottom: 8px;
  }

  .ant-upload-text {
    font-size: 15px;
    color: var(--gray-800);
    margin-bottom: 4px;
  }

  .ant-upload-hint {
    font-size: 12px;
    color: var(--gray-500);
  }
}

.zip-tip {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-warning-600);
  background: var(--color-warning-50);
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
}

.upload-progress-card {
  margin-top: 8px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-50);
  padding: 8px;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.progress-header-left {
  display: flex;
  flex-direction: row;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.progress-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.progress-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--gray-700);
  white-space: nowrap;
}

.progress-percent {
  font-size: 14px;
  font-weight: 700;
  color: var(--main-600);
}

.progress-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  &.inline-in-header {
    gap: 6px;
  }
}

.stat-pill {
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 11px;
  line-height: 1.4;
  border: 1px solid var(--gray-300);
  background: var(--gray-100);
  color: var(--gray-600);

  &.uploading {
    background: var(--main-50);
    border-color: var(--main-200);
    color: var(--main-600);
  }

  &.queued {
    background: var(--gray-100);
    border-color: var(--gray-300);
    color: var(--gray-600);
  }

  &.success {
    background: var(--color-success-50);
    border-color: var(--color-success-200);
    color: var(--color-success-600);
  }

  &.error {
    background: var(--color-error-50);
    border-color: var(--color-error-200);
    color: var(--color-error-600);
  }
}

.progress-tip {
  margin-top: 6px;
  font-size: 11px;
  color: var(--gray-500);
}

.progress-details {
  border-top: 1px dashed var(--gray-200);
  padding-top: 6px;
}

.details-list {
  max-height: 160px;
  overflow-y: auto;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
}

.detail-row {
  padding: 6px 8px;
  border-bottom: 1px solid var(--gray-100);

  &:last-child {
    border-bottom: none;
  }
}

.detail-name {
  font-size: 11px;
  color: var(--gray-700);
  font-weight: 500;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.detail-error {
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-error-600);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.toggle-progress-btn {
  color: var(--gray-500);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding-inline: 4px;

  &:hover {
    color: var(--main-600);
    background: var(--gray-100);
  }
}

/* URL Area */
.url-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.url-input-wrapper {
  width: 100%;
}

.url-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}

.url-hint {
  font-size: 12px;
  color: var(--gray-500);

  .warning-text {
    color: var(--color-warning-500);
    margin-left: 4px;
  }
}

.url-input {
  width: 100%;
  padding: 10px;
}

.add-url-btn {
  margin-left: 8px;
}

.url-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
}

.url-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--gray-50);
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  transition: all 0.2s;

  &:hover {
    background: var(--gray-100);
    border-color: var(--main-300);
  }
}

.url-icon-wrapper {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.url-icon {
  color: var(--main-500);

  &.success {
    color: var(--color-success-500);
  }

  &.error {
    color: var(--color-error-500);
    cursor: help;
  }

  &.spinning {
    animation: spin 1s linear infinite;
    color: var(--main-500);
  }
}

.url-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.url-text {
  font-size: 13px;
  color: var(--gray-700);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.url-error-msg {
  font-size: 11px;
  color: var(--color-error-500);
  margin-top: 2px;
}

.remove-url-btn {
  color: var(--gray-400);
  flex-shrink: 0;

  &:hover {
    color: var(--color-error-500);
  }
}

.url-empty-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  background: var(--gray-50);
  border: 1px dashed var(--gray-300);
  border-radius: 8px;
  color: var(--gray-500);
  font-size: 13px;
}

/* Conflict Files Panel */
.conflict-files-panel {
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  overflow: hidden;
  background: var(--gray-0);
  margin-top: 4px;
}

.panel-header {
  background: var(--gray-50);
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--gray-700);
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--gray-200);

  .icon-warning {
    color: var(--color-warning-500);
  }
}

.file-list-scroll {
  max-height: 120px;
  overflow-y: auto;
}

.conflict-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--gray-100);
  transition: background 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background: var(--gray-50);
  }
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
  font-size: 13px;

  .fname {
    font-weight: 500;
    color: var(--gray-800);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ftime {
    color: var(--gray-400);
    font-size: 12px;
    flex-shrink: 0;
  }
}

.file-actions {
  display: flex;
  gap: 4px;

  .action-btn {
    color: var(--gray-500);

    &:hover {
      color: var(--main-600);
      background: var(--main-50);
    }

    &.delete:hover {
      color: var(--color-error-500);
      background: var(--color-error-50);
    }
  }
}

.auto-index-params {
  margin-top: 8px;
  padding: 12px;
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 6px;
}

.setting-label .ant-checkbox {
  margin-right: 8px;
}

@media (max-width: 768px) {
  .top-action-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }

  .auto-index-toggle {
    padding-right: 0;
  }

  .progress-header {
    flex-direction: column;
    gap: 8px;
  }

  .progress-header-right {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
