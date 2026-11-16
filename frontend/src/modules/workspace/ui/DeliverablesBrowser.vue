<template>
  <section class="deliverables-browser" aria-label="我的成果">
    <div class="deliverables-toolbar">
      <a-input-search
        v-model:value="query"
        class="deliverables-search"
        placeholder="搜索成果文件名"
        aria-label="搜索成果文件名"
        allow-clear
        @search="search"
      />
      <a-select
        v-model:value="fileType"
        class="deliverables-filter"
        aria-label="筛选成果文件类型"
        :options="fileTypes"
        @change="search"
      />
      <a-button class="deliverables-refresh lucide-icon-btn" :loading="loading" @click="load">
        <template #icon><RefreshCw :size="16" /></template>
        刷新
      </a-button>
    </div>
    <p class="deliverables-description">对话中交付的文件会自动保存到这里，方便随时查看和下载。</p>

    <a-alert v-if="error" type="error" :message="error" show-icon>
      <template #action><a-button size="small" @click="load">重试</a-button></template>
    </a-alert>
    <div v-else-if="loading" class="deliverables-state" role="status">
      <a-spin />
      <span>正在加载成果...</span>
    </div>
    <ResourceEmptyState
      v-else-if="!items.length"
      :title="appliedQuery || fileType !== 'all' ? '没有找到匹配的成果' : '还没有成果文件'"
      :description="appliedQuery || fileType !== 'all' ? '试试其他文件名或文件类型。' : '在对话中生成文档、图片、视频等文件后，即可在这里查看。'"
      :icon="Files"
    />
    <div v-else class="deliverables-table-scroll">
      <table class="deliverables-table" aria-label="我的成果文件">
        <thead><tr>
          <th scope="col">文件名</th>
          <th scope="col">来源对话</th>
          <th scope="col">类型</th>
          <th scope="col">大小</th>
          <th scope="col">生成时间</th>
          <th scope="col"><span class="sr-only">操作</span></th>
        </tr></thead>
        <tbody><tr v-for="file in items" :key="file.id">
          <td>
            <button type="button" class="deliverable-name" :title="file.name" @click="openPreview(file)">
              <FileTypeIcon :name="file.name" :size="22" />
              <span>{{ file.name }}</span>
            </button>
          </td>
          <td>
            <RouterLink class="deliverable-source" :to="`/agent/${file.thread_id}`" :title="file.thread_title || '来源对话'">
              <span>{{ file.thread_title || '来源对话' }}</span><ArrowUpRight :size="13" aria-hidden="true" />
            </RouterLink>
          </td>
          <td class="deliverable-meta">{{ getFileExtensionLabel(file.name) || '文件' }}</td>
          <td class="deliverable-meta">{{ formatFileSize(file.size) }}</td>
          <td class="deliverable-meta">{{ formatDateTime(file.created_at) }}</td>
          <td class="deliverable-actions">
            <a-button type="text" class="lucide-icon-btn" :aria-label="`下载 ${file.name}`" :loading="downloadingIds.has(file.id)" @click="download(file)">
              <template #icon><Download :size="16" /></template>
            </a-button>
          </td>
        </tr></tbody>
      </table>
    </div>
    <a-pagination
      v-if="total > pageSize && !error"
      class="deliverables-pagination"
      :current="page"
      :page-size="pageSize"
      :total="total"
      :show-size-changer="false"
      :disabled="loading"
      @change="changePage"
    />

    <a-modal
      :open="Boolean(selectedFile)"
      :title="selectedFile?.name || '成果预览'"
      :footer="null"
      width="960px"
      :style="{ maxWidth: '94vw', top: '5vh' }"
      :body-style="{ height: '75vh', overflow: 'hidden' }"
      wrap-class-name="deliverable-preview-modal"
      @cancel="closePreview"
    >
      <div v-if="previewLoading" class="deliverables-state" role="status"><a-spin /><span>正在加载文件...</span></div>
      <div v-else-if="previewError" class="deliverables-state" role="alert">
        <span>{{ previewError }}</span>
        <a-button @click="openPreview(selectedFile)">重试</a-button>
      </div>
      <video v-else-if="previewFile?.previewType === 'video'" class="deliverable-media" :src="previewFile.previewUrl" controls />
      <audio v-else-if="previewFile?.previewType === 'audio'" class="deliverable-media" :src="previewFile.previewUrl" controls />
      <AgentFilePreview
        v-else-if="previewFile"
        :file="previewFile"
        :file-path="selectedFile.name"
        :show-close="false"
        :show-download="true"
        :full-height="true"
        @download="download(selectedFile)"
      />
      <a-button v-if="previewFile?.previewType === 'video' || previewFile?.previewType === 'audio'" class="media-download lucide-icon-btn" :loading="downloadingIds.has(selectedFile.id)" @click="download(selectedFile)">
        <template #icon><Download :size="16" /></template>下载文件
      </a-button>
    </a-modal>
  </section>
</template>

<script setup>
import { onActivated, onDeactivated, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowUpRight, Download, Files, RefreshCw } from '@lucide/vue'
import { deliverablesApi } from '@/apis/deliverables_api'
import AgentFilePreview from '@/modules/session/ui/workspace/AgentFilePreview.vue'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import ResourceEmptyState from '@/shared/ui/ResourceEmptyState.vue'
import { normalizePreviewResponse } from '@/shared/lib/file_preview'
import { formatFileSize, getFileExtensionLabel, parseDownloadFilename } from '@/shared/lib/file_utils'
import { formatDateTime } from '@/shared/lib/time'

const fileTypes = [
  { value: 'all', label: '全部文件类型' },
  { value: 'document', label: '文档' },
  { value: 'image', label: '图片' },
  { value: 'video', label: '视频' },
  { value: 'audio', label: '音频' },
  { value: 'other', label: '其他' }
]
const query = ref('')
const appliedQuery = ref('')
const fileType = ref('all')
const page = ref(1)
const pageSize = 20
const items = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const downloadingIds = ref(new Set())
const selectedFile = ref(null)
const previewFile = ref(null)
const previewLoading = ref(false)
const previewError = ref('')
let listRequest = 0
let previewRequest = 0

/** 按服务端分页读取本人已保存的成果，忽略过时请求。 */
const load = async () => {
  const request = ++listRequest
  loading.value = true
  error.value = ''
  try {
    const result = await deliverablesApi.list({ query: appliedQuery.value, fileType: fileType.value, page: page.value, pageSize })
    if (request !== listRequest) return
    items.value = result.items
    total.value = result.total
  } catch (cause) {
    if (request !== listRequest) return
    items.value = []
    total.value = 0
    error.value = cause?.message || '加载成果失败，请重试'
  } finally {
    if (request === listRequest) loading.value = false
  }
}

/** 应用搜索或分类时回到第一页。 */
const search = () => {
  appliedQuery.value = query.value.trim()
  page.value = 1
  return load()
}

/** 翻页后读取对应成果。 */
const changePage = (value) => {
  page.value = value
  return load()
}

/** 关闭预览时释放认证读取产生的临时 URL。 */
const closePreview = () => {
  previewRequest++
  if (previewFile.value?.previewUrl) window.URL.revokeObjectURL(previewFile.value.previewUrl)
  selectedFile.value = null
  previewFile.value = null
  previewLoading.value = false
  previewError.value = ''
}

/** 通过认证请求预览副本；音视频仅使用本地 blob URL。 */
const openPreview = async (file) => {
  closePreview()
  selectedFile.value = file
  previewLoading.value = true
  const request = previewRequest
  try {
    const mediaType = file.mime_type?.split('/')[0]
    let preview
    if (['video', 'audio'].includes(mediaType)) {
      const response = await deliverablesApi.content(file.id)
      const blob = await response.blob()
      if (request !== previewRequest) return
      preview = { previewType: mediaType, previewUrl: window.URL.createObjectURL(blob) }
    } else {
      const response = await deliverablesApi.preview(file.id)
      preview = await normalizePreviewResponse(response, { path: file.name })
    }
    if (request !== previewRequest) {
      if (preview.previewUrl) window.URL.revokeObjectURL(preview.previewUrl)
      return
    }
    previewFile.value = preview
  } catch (cause) {
    if (request === previewRequest) previewError.value = cause?.message || '加载文件失败，请重试'
  } finally {
    if (request === previewRequest) previewLoading.value = false
  }
}

/** 下载认证后的成果副本并保留原始文件名。 */
const download = async (file) => {
  if (downloadingIds.value.has(file.id)) return
  downloadingIds.value.add(file.id)
  try {
    const response = await deliverablesApi.download(file.id)
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = parseDownloadFilename(response.headers.get('content-disposition')) || file.name
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch (cause) {
    message.error(cause?.message || '下载成果失败')
  } finally {
    downloadingIds.value.delete(file.id)
  }
}

onActivated(load)
onDeactivated(closePreview)
onUnmounted(() => { listRequest++; closePreview() })
</script>

<style scoped lang="less">
.deliverables-browser { flex: 1; min-width: 0; min-height: 0; overflow: auto; padding: 20px var(--page-padding); background: var(--gray-0); }
.deliverables-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.deliverables-search { width: 300px; }
.deliverables-filter { width: 145px; }
.deliverables-refresh { margin-left: auto; }
.deliverables-description { margin: 14px 0 20px; color: var(--color-text-secondary); font-size: 13px; }
.deliverables-state { display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 16px; min-height: 260px; height: 100%; color: var(--color-text-secondary); }
.deliverables-table-scroll { overflow-x: auto; }
.deliverables-table { width: 100%; min-width: 680px; border-collapse: collapse; text-align: left; font-size: 13px;
  th, td { padding: 14px 12px; border-bottom: 1px solid var(--gray-100); }
  th { font-weight: 400; color: var(--color-text-secondary); white-space: nowrap; }
  th:first-child { width: 36%; }
  th:nth-child(2) { width: 24%; }
  tbody tr:hover { background: var(--gray-10); }
}
.deliverable-name, .deliverable-source { display: flex; align-items: center; gap: 10px; min-width: 0; max-width: 360px; color: var(--color-text); }
.deliverable-name { border: 0; padding: 0; background: transparent; font: inherit; text-align: left; cursor: pointer; }
.deliverable-name span, .deliverable-source span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.deliverable-name :deep(svg), .deliverable-source svg { flex-shrink: 0; }
.deliverable-name:hover, .deliverable-source:hover { color: var(--main-color); }
.deliverable-source { max-width: 240px; gap: 4px; color: var(--color-text-secondary); font-size: 12px; }
.deliverable-meta { color: var(--color-text-secondary); white-space: nowrap; }
.deliverable-actions { text-align: right; }
.deliverables-pagination { margin-top: 20px; display: flex; justify-content: flex-end; }
.deliverable-media { display: block; width: 100%; max-height: calc(100% - 48px); background: var(--gray-25); }
.media-download { margin-top: 12px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
@media (max-width: 768px) {
  .deliverables-browser { padding: 16px; }
  .deliverables-search { width: 100%; }
  .deliverables-refresh { margin-left: auto; }
  .deliverable-name { max-width: 240px; }
}
</style>
