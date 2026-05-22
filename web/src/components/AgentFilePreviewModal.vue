<template>
  <a-modal
    v-model:open="visible"
    width="800px"
    :style="{ maxWidth: '90vw', top: '5vh' }"
    :bodyStyle="{ maxHeight: '90vh', overflow: 'auto' }"
    :footer="null"
    :closable="false"
    wrapClassName="agent-file-preview-modal"
    @cancel="closePreview"
  >
    <AgentFilePreview
      v-if="previewFile"
      :file="previewFile"
      :filePath="previewFilePath"
      :showClose="true"
      :showDownload="true"
      :showFullscreen="true"
      @download="downloadFile"
      @close="closePreview"
    />
  </a-modal>
</template>

<script setup>
import { ref, watch, onUnmounted } from 'vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import { getViewerFileContent, downloadViewerFile } from '@/apis/viewer_filesystem'
import { useChatUIStore } from '@/stores/chatUI'
import { parseDownloadFilename } from '@/utils/file_utils'

const props = defineProps({
  threadId: {
    type: String,
    default: null
  },
  agentId: {
    type: String,
    default: null
  },
  agentConfigId: {
    type: [String, Number],
    default: null
  }
})

const chatUIStore = useChatUIStore()

const visible = ref(false)
const previewFile = ref(null)
const previewFilePath = ref('')

const revokePreviewUrl = () => {
  const previewUrl = previewFile.value?.previewUrl
  if (previewUrl) {
    window.URL.revokeObjectURL(previewUrl)
  }
}

const closePreview = () => {
  revokePreviewUrl()
  visible.value = false
  previewFile.value = null
  previewFilePath.value = ''
}

const openPreview = async (filePath) => {
  if (!props.threadId || !filePath) return

  revokePreviewUrl()
  previewFilePath.value = filePath

  const fileName = filePath.split('/').pop() || filePath
  const fileData = {
    path: filePath,
    name: fileName,
    type: 'file'
  }

  previewFile.value = {
    ...fileData,
    content: 'Loading...',
    supported: true,
    previewType: 'text',
    message: '',
    previewUrl: ''
  }
  visible.value = true

  try {
    const res = await getViewerFileContent(
      props.threadId,
      filePath,
      props.agentId,
      props.agentConfigId
    )
    const previewType = res?.preview_type || 'text'
    let previewUrl = ''

    if ((previewType === 'image' || previewType === 'pdf') && res?.supported) {
      const response = await downloadViewerFile(
        props.threadId,
        filePath,
        props.agentId,
        props.agentConfigId
      )
      const blob = await response.blob()
      previewUrl = window.URL.createObjectURL(blob)
    }

    previewFile.value = {
      ...fileData,
      content: res?.content ?? '',
      supported: res?.supported !== false,
      previewType,
      message: res?.message || '',
      previewUrl
    }
  } catch (error) {
    previewFile.value = {
      ...fileData,
      content: `Error loading file: ${error?.message || 'unknown error'}`,
      supported: false,
      previewType: 'unsupported',
      message: error?.message || '文件预览失败',
      previewUrl: ''
    }
  }
}

const downloadFile = async (file) => {
  if (!props.threadId || !file?.path) return

  try {
    const response = await downloadViewerFile(
      props.threadId,
      file.path,
      props.agentId,
      props.agentConfigId
    )
    const blob = await response.blob()
    const contentDisposition =
      response.headers.get('Content-Disposition') || response.headers.get('content-disposition')
    const filename = parseDownloadFilename(contentDisposition) || file.name
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.error('下载文件失败:', error)
  }
}

watch(
  () => chatUIStore.previewFileTriggerTime,
  (newVal) => {
    if (newVal && chatUIStore.previewFilePath) {
      openPreview(chatUIStore.previewFilePath)
    }
  }
)

onUnmounted(() => {
  revokePreviewUrl()
})
</script>
