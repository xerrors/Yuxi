<template>
  <component
    :is="embedded ? 'section' : Modal"
    v-bind="containerProps"
    @update:open="visible = $event"
    @after-open-change="afterOpenChange"
  >
    <div class="modal-title-wrapper">
      <!-- 左侧：文件名和图标 -->
      <div class="file-title">
        <FileTypeIcon :name="file?.filename" :size="18" />
        <span class="file-name" :title="file?.filename || '文件详情'">{{ file?.filename || '文件详情' }}</span>
      </div>

      <div class="header-controls">
        <!-- 字符数/片段数显示在视图选择器左边 -->
        <span v-if="viewInfoText && !markdownEditor.active" class="view-info expanded-controls">{{ viewInfoText }}</span>

        <!-- 文件视图与下载操作 -->
        <a-dropdown
          v-if="file && !markdownEditor.active"
          v-model:open="fileActionsOpen"
          :trigger="['click']"
          overlay-class-name="knowledge-file-actions"
        >
          <a-button
            ref="fileActionsTrigger"
            class="view-controls expanded-controls"
            :aria-label="currentViewOption ? `文件操作，当前视图为${currentViewOption.title}` : '文件操作'"
            @keydown.esc="closeFileActions($event, false)"
          >
            <component :is="currentViewOption?.icon || FileText" :size="16" aria-hidden="true" />
            <span>{{ currentViewOption?.title || '文件操作' }}</span>
            <ChevronDown :size="14" aria-hidden="true" />
          </a-button>
          <template #overlay>
            <a-menu :selected-keys="[viewMode]" @click="finishFileAction($event, false)" @keydown.esc="closeFileActions($event, false)">
              <a-menu-item
                v-for="option in viewModeOptions"
                :key="option.value"
                @click="viewMode = option.value"
              >
                <template #icon><component :is="option.icon" :size="16" aria-hidden="true" /></template>
                {{ option.title }}
              </a-menu-item>
              <a-menu-divider v-if="viewModeOptions.length" />
              <a-menu-item key="download-original" :disabled="!file.file_id" @click="handleDownloadMenuClick({ key: 'original' })">
                <template #icon><Download :size="16" aria-hidden="true" /></template>
                下载原文
              </a-menu-item>
              <a-menu-item key="download-markdown" :disabled="contentState.loading || !mergedContent" @click="handleDownloadMenuClick({ key: 'markdown' })">
                <template #icon><FileText :size="16" aria-hidden="true" /></template>
                下载 Markdown
              </a-menu-item>
              <template v-if="canEditMarkdown">
                <a-menu-divider />
                <a-menu-item key="edit-file" @click="startMarkdownEdit()">
                  <template #icon><Pencil :size="16" aria-hidden="true" /></template>
                  编辑文件
                </a-menu-item>
              </template>
            </a-menu>
          </template>
        </a-dropdown>

        <a-dropdown v-if="file && !markdownEditor.active" v-model:open="compactActionsOpen" :trigger="['click']" overlay-class-name="knowledge-file-actions">
          <a-button
            ref="compactActionsTrigger"
            type="text"
            class="compact-controls"
            title="更多文件操作"
            aria-label="更多文件操作"
            @keydown.esc="closeFileActions($event, true)"
          >
            <MoreHorizontal :size="18" />
          </a-button>
          <template #overlay>
            <a-menu :selected-keys="[viewMode]" @click="finishFileAction($event, true)" @keydown.esc="closeFileActions($event, true)">
              <a-menu-item
                v-for="option in viewModeOptions"
                :key="option.value"
                @click="viewMode = option.value"
              >
                <template #icon><component :is="option.icon" :size="16" aria-hidden="true" /></template>
                {{ option.title }}
              </a-menu-item>
              <a-menu-divider v-if="viewModeOptions.length" />
              <a-menu-item
                key="download-original"
                :disabled="!file.file_id"
                @click="handleDownloadMenuClick({ key: 'original' })"
              >
                <template #icon><Download :size="16" aria-hidden="true" /></template>
                下载原文
              </a-menu-item>
              <a-menu-item
                key="download-markdown"
                :disabled="contentState.loading || !mergedContent"
                @click="handleDownloadMenuClick({ key: 'markdown' })"
              >
                <template #icon><FileText :size="16" aria-hidden="true" /></template>
                下载 Markdown
              </a-menu-item>
              <template v-if="canEditMarkdown">
                <a-menu-divider />
                <a-menu-item key="edit-file" @click="startMarkdownEdit()">
                  <template #icon><Pencil :size="16" aria-hidden="true" /></template>
                  编辑文件
                </a-menu-item>
              </template>
            </a-menu>
          </template>
        </a-dropdown>

        <!-- 自定义关闭按钮 -->
        <button
          type="button"
          class="custom-close-btn"
          aria-label="关闭预览"
          @click="visible = false"
        >
          <X :size="16" />
        </button>
      </div>
    </div>
    <div v-if="markdownEditor.active" class="markdown-editor-panel" :aria-busy="markdownEditor.loading || markdownEditor.saving">
      <div class="markdown-editor-toolbar">
        <div class="markdown-editor-view">
          <a-radio-group v-model:value="markdownEditorView" name="knowledge-markdown-editor-view" size="small" :disabled="!markdownEditor.loaded" aria-label="文件编辑视图">
            <a-radio-button value="edit">编辑</a-radio-button>
            <a-radio-button value="preview">预览</a-radio-button>
          </a-radio-group>
          <span class="markdown-editor-status" role="status">{{ markdownEditor.saving ? '正在保存…' : markdownEditor.conflict ? '结果需确认' : markdownDirty ? '未保存' : '' }}</span>
        </div>
        <div class="markdown-editor-actions">
          <a-button @click="cancelMarkdownEdit">{{ markdownEditor.saving ? '关闭' : '取消编辑' }}</a-button>
          <a-button type="primary" :loading="markdownEditor.saving" :disabled="!canSaveMarkdown" @click="requestMarkdownSave">保存</a-button>
        </div>
      </div>
      <a-alert v-if="markdownEditor.error" type="error" show-icon :message="markdownEditor.error">
        <template v-if="markdownEditor.conflict" #action>
          <a-button size="small" :loading="latestMarkdown.loading" @click="readLatestMarkdown">查看最新版本</a-button>
        </template>
      </a-alert>
      <a-alert v-if="markdownEditor.saving && jobsStore.lastError" type="warning" show-icon message="暂时无法确认保存结果。可重试查询，或下载草稿后关闭。">
        <template #action><a-button size="small" @click="jobsStore.loadJobs()">重试查询</a-button></template>
      </a-alert>
      <div v-if="markdownEditor.loading" class="loading-container"><a-spin tip="正在读取 Markdown…" /></div>
      <div v-else-if="!markdownEditor.loaded" class="empty-content">
        <a-button @click="startMarkdownEdit">重试读取</a-button>
      </div>
      <template v-else>
        <p v-if="!markdownEditor.hasParsed" class="markdown-editor-hint">暂无解析内容，可在此编写 Markdown。</p>
        <AgentFilePreview
          v-show="markdownEditorView === 'edit'"
          ref="markdownPreview"
          :file="{ content: markdownEditor.content, previewType: 'markdown', supported: true }"
          :file-path="`${file?.filename || 'document'}.md`"
          :draft="markdownEditor.draft"
          editable
          :saving="markdownEditor.saving"
          :show-header="false"
          :show-download="false"
          :show-edit-actions="false"
          full-height
          borderless
          @draft-change="markdownEditor.draft = $event"
        />
        <div v-if="markdownEditorView === 'preview'" class="markdown-draft-preview flat-md-preview">
          <MarkdownDocumentPreview v-if="markdownEditor.draft" :content="markdownEditor.draft" />
          <div v-else class="empty-content">Markdown 内容为空</div>
        </div>
      </template>
    </div>
    <div v-else-if="basicLoading" class="loading-container">
      <a-spin tip="正在加载文档内容..." />
    </div>
    <div v-else-if="detailError" class="empty-content">
      <p>{{ detailError }}</p>
    </div>
    <div v-else-if="file && hasAvailableView" class="file-detail-content">
      <div v-if="viewMode === 'source'" class="content-panel source-panel">
        <AgentFilePreview
          :file="sourcePreviewFile"
          :file-path="file?.filename || ''"
          :status="sourcePreview.loading ? 'loading' : ''"
          loading-message="正在加载文件内容..."
          :show-header="false"
          :show-download="false"
          :show-inline-html-controls="true"
          :full-height="true"
          :borderless="true"
          container-class="source-preview-container"
          content-class="source-preview-content"
        />
      </div>

      <!-- Markdown 模式 -->
      <div
        v-else-if="['markdown', 'markdown-source'].includes(viewMode)"
        class="content-panel flat-md-preview"
      >
        <div v-if="contentState.loading" class="loading-container">
          <a-spin tip="正在加载解析内容..." />
        </div>
        <template v-else>
          <p v-if="(chunkId || referenceSource) && contentState.loaded && locationNotice" class="location-notice">
            {{ locationNotice }}
          </p>
          <p v-else-if="chunkId && contentState.loaded && !targetChunk" class="location-notice">
            文档已重新索引，原片段位置已失效，请重新检索。
          </p>
          <p
            v-else-if="chunkId && contentState.loaded && !targetLines.start"
            class="location-notice"
          >
            此片段尚无入库行号，重新索引后可定位高亮。
          </p>
          <MarkdownDocumentPreview
            v-if="mergedContent"
            :content="mergedContent"
            :source="viewMode === 'markdown-source'"
            :start-line="targetLines.start"
            :end-line="targetLines.end"
            class="markdown-content"
          />
          <div v-else class="empty-content">
            <p>{{ contentState.error || '暂无文件内容' }}</p>
          </div>
        </template>
      </div>

      <ChunkBrowser
        v-else-if="viewMode === 'chunks'"
        class="chunks-panel"
        :chunks="contentState.lines"
        :loading="contentState.loading"
        :error="contentState.error"
        :editable="canEditMarkdown"
        @edit="startMarkdownEdit(locationNotice ? 1 : $event.start_line)"
      />
    </div>

    <div v-else-if="file" class="empty-content">
      <p>暂无文件内容</p>
    </div>
  </component>
  <a-modal
    v-model:open="markdownSaveConfirmOpen"
    title="保存 Markdown"
    ok-text="保存"
    cancel-text="取消"
    @ok="submitMarkdownSave"
  >
    保存将撤销此文件的分块、普通检索和图谱，保留原件及解析图片。保存完成后需要重新入库并构建图谱。
  </a-modal>
  <a-modal
    v-model:open="latestMarkdown.open"
    title="核对最新 Markdown"
    width="800px"
    :ok-text="latestMatchesDraft ? '确认文件内容' : '保留草稿并继续'"
    cancel-text="返回编辑"
    :ok-button-props="{ disabled: latestMarkdown.loading || !latestMarkdown.data }"
    @ok="acceptLatestMarkdown"
  >
    <p>{{ latestMatchesDraft ? '文件已包含当前草稿，无需再次保存。' : '继续保存将以当前草稿覆盖下方最新内容。请先核对，草稿不会被替换。' }}</p>
    <div class="latest-markdown-preview flat-md-preview">
      <MarkdownDocumentPreview v-if="latestMarkdown.data?.content" :content="latestMarkdown.data.content" source />
      <div v-else class="empty-content">最新 Markdown 内容为空</div>
    </div>
  </a-modal>
  <a-modal
    v-model:open="markdownExitOpen"
    :title="markdownEditor.saving ? '保存仍在进行' : markdownEditor.conflict ? '关闭编辑器？' : '放弃未保存的修改？'"
    :ok-text="markdownEditor.saving ? '关闭编辑器' : '放弃修改'"
    :cancel-text="markdownEditor.saving ? '继续等待' : '继续编辑'"
    @ok="confirmMarkdownExit"
  >
    <p>{{ markdownEditor.saving ? '关闭不会取消保存。若任务失败，当前草稿将无法从此弹窗恢复；建议先下载草稿，并在后台任务或文件详情确认结果。' : markdownEditor.conflict ? '保存结果尚未确认。关闭后将丢弃此处的草稿，请先下载草稿，并稍后重新查看文件。' : '关闭后，当前草稿将丢失。你可以继续编辑，或先下载草稿。' }}</p>
    <a-button @click="downloadMarkdownDraft">下载草稿</a-button>
  </a-modal>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'
import { useBackgroundJobsStore } from '@/modules/background-jobs'
import { useUserStore } from '@/modules/identity'
import { getWorkspaceKnowledgeFileContent } from '@/apis/workspace_api'
import { mergeChunks } from '@/modules/knowledge/model/chunkUtils'
import { getPreviewTypeByPath, normalizePreviewResponse } from '@/shared/lib/file_preview'
import { parseDownloadFilename } from '@/shared/lib/file_utils'
import {
  canPreviewChunks,
  canPreviewOriginal,
  canPreviewParsed,
  getDefaultDetailView
} from '@/modules/knowledge/model/knowledge_file_policy'
import MarkdownDocumentPreview from '@/modules/workspace/ui/MarkdownDocumentPreview.vue'
import ChunkBrowser from './ChunkBrowser.vue'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import AgentFilePreview from '@/modules/session/ui/workspace/AgentFilePreview.vue'
import { Download, ChevronDown, FileSearch, FileText, Rows3, Code2, X, MoreHorizontal, Pencil } from '@lucide/vue'

const props = defineProps({
  editable: { type: Boolean, default: false },
  editRequested: { type: Boolean, default: false },
  referenceSource: { type: Object, default: null },
  embedded: { type: Boolean, default: false },
  chunkId: { type: String, default: '' },
  startLine: { type: Number, default: null },
  endLine: { type: Number, default: null },
  open: {
    type: Boolean,
    default: false
  },
  kbId: {
    type: [String, Number],
    default: ''
  },
  fileId: {
    type: [String, Number],
    default: ''
  }
})

const emit = defineEmits(['update:open', 'update:editRequested', 'closed', 'saved'])

const visible = computed({
  get: () => props.open,
  set: (value) => {
    if (!value) {
      requestMarkdownExit(true)
      return
    }
    emit('update:open', value)
  }
})

const containerProps = computed(() =>
  props.embedded
    ? { class: 'file-detail-embedded' }
    : {
        open: visible.value,
        width: 'min(1000px, calc(100vw - 48px))',
        footer: null,
        closable: false,
        maskClosable: true,
        keyboard: true,
        wrapClassName: 'file-detail',
        bodyStyle: { height: '80vh', padding: '0', display: 'flex', flexDirection: 'column' }
      }
)

const jobsStore = useBackgroundJobsStore()
const userStore = useUserStore()
const markdownPreview = ref(null)
const markdownSaveConfirmOpen = ref(false)
const markdownEditorView = ref('edit')
const markdownExitOpen = ref(false)
let markdownExitAction = null
const latestMarkdown = ref({ open: false, loading: false, data: null })
const latestMatchesDraft = computed(() => latestMarkdown.value.data?.has_parsed_markdown && latestMarkdown.value.data.content === markdownEditor.value.draft)
const markdownEditor = ref({ active: false, loading: false, loaded: false, content: '', draft: '', revision: '', hasParsed: false, error: '', conflict: false, saving: false })
let markdownRequestSeq = 0
const markdownStableStatuses = new Set(['uploaded', 'parsed', 'done', 'indexed', 'error_parsing', 'error_indexing', 'failed'])
const canEditMarkdown = computed(() => props.editable && file.value && !file.value.is_folder && markdownStableStatuses.has(file.value.status))
const markdownDirty = computed(() => markdownEditor.value.loaded && markdownEditor.value.draft !== markdownEditor.value.content)
const markdownExitRequired = computed(() => markdownEditor.value.active && (markdownDirty.value || markdownEditor.value.saving || markdownEditor.value.conflict))
const canSaveMarkdown = computed(() => canEditMarkdown.value && markdownEditor.value.loaded && !markdownEditor.value.loading && !markdownEditor.value.saving && !markdownEditor.value.conflict && (!markdownEditor.value.hasParsed || markdownEditor.value.draft !== markdownEditor.value.content))

/** 严格读取完整解析对象，任何读取失败都不能替换为空稿或分块。 */
const startMarkdownEdit = async (startLine = 1) => {
  if (!canEditMarkdown.value || markdownEditor.value.saving) return
  markdownEditorView.value = 'edit'
  const requestId = ++markdownRequestSeq
  const token = userStore.token
  markdownEditor.value = { active: true, loading: true, loaded: false, content: '', draft: '', revision: '', hasParsed: false, error: '', conflict: false, saving: false }
  try {
    const data = await documentApi.getDocumentMarkdown(props.kbId, props.fileId)
    if (requestId !== markdownRequestSeq || userStore.token !== token || !props.open || !props.editable) return
    if (typeof data.content !== 'string' || typeof data.revision !== 'string' || typeof data.has_parsed_markdown !== 'boolean') throw new Error('Markdown 响应不完整，请重试读取')
    Object.assign(markdownEditor.value, { loading: false, loaded: true, content: data.content, draft: data.content, revision: data.revision, hasParsed: data.has_parsed_markdown })
    await nextTick()
    markdownPreview.value?.startEditing(startLine)
  } catch (error) {
    if (requestId === markdownRequestSeq && userStore.token === token) markdownEditor.value.error = error.message || '读取 Markdown 失败'
  } finally {
    if (requestId === markdownRequestSeq) markdownEditor.value.loading = false
  }
}

/** 退出统一经过草稿确认；保存中的任务由后台继续执行。 */
const requestMarkdownExit = (closePreview) => {
  if (markdownExitRequired.value) {
    markdownExitAction = () => finishMarkdownExit(closePreview)
    markdownExitOpen.value = true
    return
  }
  finishMarkdownExit(closePreview)
}

/** 仅在明确确认后丢弃本地编辑态。 */
const finishMarkdownExit = (closePreview) => {
  markdownRequestSeq += 1
  markdownEditor.value.active = false
  markdownEditor.value.error = ''
  markdownSaveConfirmOpen.value = false
  latestMarkdown.value.open = false
  if (closePreview) emit('update:open', false)
}

/** 取消编辑与关闭预览共用同一份脏稿保护。 */
const cancelMarkdownEdit = () => requestMarkdownExit(markdownEditor.value.saving)

/** 执行此次确认对应的退出动作。 */
const confirmMarkdownExit = () => {
  markdownExitOpen.value = false
  markdownExitAction?.()
  markdownExitAction = null
}

/** 导出当前草稿，允许在保存结果未知时自行恢复。 */
const downloadMarkdownDraft = () => {
  const blob = new Blob([markdownEditor.value.draft], { type: 'text/markdown;charset=utf-8' })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${(file.value?.filename || 'document').replace(/\.[^.]+$/, '')}-草稿.md`
  link.click()
  window.URL.revokeObjectURL(url)
}

/** 冲突后读取最新版本供核对，读取过程不改变草稿或原修订。 */
const readLatestMarkdown = async () => {
  if (latestMarkdown.value.loading || markdownEditor.value.saving) return
  const requestId = markdownRequestSeq
  latestMarkdown.value = { open: false, loading: true, data: null }
  try {
    const data = await documentApi.getDocumentMarkdown(props.kbId, props.fileId)
    if (requestId !== markdownRequestSeq) return
    if (typeof data.content !== 'string' || typeof data.revision !== 'string' || typeof data.has_parsed_markdown !== 'boolean') throw new Error('Markdown 响应不完整，请重试读取')
    latestMarkdown.value = { open: true, loading: false, data }
  } catch (error) {
    if (requestId === markdownRequestSeq) {
      latestMarkdown.value.loading = false
      markdownEditor.value.error = error.message || '读取最新版本失败，请重试'
    }
  }
}

/** 用户核对后显式采纳最新修订，后续保存仍执行并发检查。 */
const acceptLatestMarkdown = async () => {
  const data = latestMarkdown.value.data
  if (!data || latestMarkdown.value.loading) return
  Object.assign(markdownEditor.value, { content: data.content, revision: data.revision, hasParsed: data.has_parsed_markdown, conflict: false, error: '' })
  latestMarkdown.value.open = false
  if (data.has_parsed_markdown && data.content === markdownEditor.value.draft) {
    message.success('文件已包含当前草稿')
    markdownEditor.value.active = false
    emit('saved')
    emit('update:open', false)
    return
  }
  await nextTick()
  markdownPreview.value?.startEditing()
}

/** 已入库文件在提交前明确说明需要重新入库和构图。 */
const requestMarkdownSave = () => {
  if (!canSaveMarkdown.value) return
  if (Number(file.value.chunk_count) > 0 || ['indexed', 'done', 'error_indexing'].includes(file.value.status)) {
    markdownSaveConfirmOpen.value = true
  } else {
    void submitMarkdownSave()
  }
}

/** 保存回执只代表入队，持久作业成功后才通知页面刷新。 */
const submitMarkdownSave = async () => {
  if (!canSaveMarkdown.value) return
  markdownSaveConfirmOpen.value = false
  markdownEditor.value.saving = true
  markdownEditor.value.error = ''
  const requestId = markdownRequestSeq
  const token = userStore.token
  const kbId = props.kbId
  const fileId = props.fileId
  const isCurrent = () => requestId === markdownRequestSeq && props.open && props.editable && userStore.token === token && props.kbId === kbId && props.fileId === fileId
  let jobId
  const registerJob = jobsStore.createJobRegistration(() => {
    if (!isCurrent()) return
    const job = jobsStore.jobs.find((item) => item.id === jobId)
    markdownEditor.value.saving = false
    if (job?.status === 'success') {
      message.success('Markdown 已保存，请按需重新入库并构建图谱')
      markdownEditor.value.active = false
      markdownExitOpen.value = false
      emit('saved')
      emit('update:open', false)
    } else {
      markdownEditor.value.conflict = true
      const detail = (typeof job?.error === 'string' ? job.error : job?.error?.message) || job?.message || '保存未完成'
      markdownEditor.value.error = `保存结果需确认：${detail}。草稿已保留，请核对最新版本。`
    }
  })
  try {
    const result = await documentApi.saveDocumentMarkdown(kbId, fileId, markdownEditor.value.revision, markdownEditor.value.draft)
    if (!isCurrent()) return
    if (!result?.job_id) throw new Error('保存任务缺少作业回执，请重试')
    jobId = result.job_id
    await registerJob(result)
  } catch (error) {
    if (!isCurrent()) return
    markdownEditor.value.saving = false
    markdownEditor.value.conflict = true
    markdownEditor.value.error = error.status === 409
      ? '文档已更新或正在处理，草稿已保留。请核对最新版本后再保存。'
      : `保存结果需确认：${error.message || '请求未完成'}。草稿已保留，请核对最新版本。`
  }
}

const file = ref(null)
const fileActionsOpen = ref(false)
const compactActionsOpen = ref(false)
const fileActionsTrigger = ref(null)
const compactActionsTrigger = ref(null)
/** 关闭文件菜单并将键盘焦点还给入口。 */
const closeFileActions = (event, compact) => {
  if (!(compact ? compactActionsOpen.value : fileActionsOpen.value)) return
  event.preventDefault()
  event.stopPropagation()
  if (compact) compactActionsOpen.value = false
  else fileActionsOpen.value = false
  nextTick(() => (compact ? compactActionsTrigger.value : fileActionsTrigger.value)?.$el?.focus())
}
/** 完成菜单操作后仅为键盘操作恢复入口焦点。 */
const finishFileAction = ({ domEvent }, compact) => {
  if (compact) compactActionsOpen.value = false
  else fileActionsOpen.value = false
  if (domEvent?.type === 'keydown') {
    nextTick(() => (compact ? compactActionsTrigger.value : fileActionsTrigger.value)?.$el?.focus())
  }
}
const basicLoading = ref(false)
const detailError = ref('')
const downloadingOriginal = ref(false)
const downloadingMarkdown = ref(false)
const contentState = ref({
  loading: false,
  loaded: false,
  lines: [],
  content: '',
  lineLocation: null,
  error: ''
})
const sourcePreview = ref({
  loading: false,
  url: '',
  content: '',
  type: '',
  message: '',
  supported: true
})

let basicRequestSeq = 0
let contentRequestSeq = 0
let sourceRequestSeq = 0

const revokeSourcePreviewUrl = () => {
  if (sourcePreview.value.url) {
    window.URL.revokeObjectURL(sourcePreview.value.url)
    sourcePreview.value.url = ''
  }
}

const resetContentState = () => {
  contentState.value = {
    loading: false,
    loaded: false,
    lines: [],
    content: '',
    lineLocation: null,
    error: ''
  }
}

const resetSourcePreview = () => {
  sourceRequestSeq += 1
  revokeSourcePreviewUrl()
  sourcePreview.value = {
    loading: false,
    url: '',
    content: '',
    type: '',
    message: '',
    supported: true
  }
}

const resetLocalState = () => {
  basicRequestSeq += 1
  contentRequestSeq += 1
  markdownRequestSeq += 1
  markdownEditor.value.active = false
  markdownEditor.value.saving = false
  markdownSaveConfirmOpen.value = false
  markdownExitOpen.value = false
  markdownExitAction = null
  latestMarkdown.value = { open: false, loading: false, data: null }
  file.value = null
  basicLoading.value = false
  detailError.value = ''
  downloadingOriginal.value = false
  downloadingMarkdown.value = false
  resetContentState()
  resetSourcePreview()
  viewMode.value = 'markdown'
}

const normalizeFileMeta = (meta = {}) => ({
  ...meta,
  file_id: meta.file_id || String(props.fileId || ''),
  kb_id: meta.kb_id || String(props.kbId || ''),
  filename: meta.filename || meta.original_filename || String(props.fileId || ''),
  file_size: meta.file_size ?? meta.size ?? 0,
  has_original_file:
    'has_original_file' in meta
      ? Boolean(meta.has_original_file)
      : Boolean(meta.minio_url || meta.path),
  has_parsed_markdown:
    'has_parsed_markdown' in meta ? Boolean(meta.has_parsed_markdown) : Boolean(meta.markdown_file)
})

const ensureApiSuccess = (data, fallbackMessage) => {
  if (data?.status === 'failed') {
    throw new Error(data.message || fallbackMessage)
  }
}

// 视图模式
const viewMode = ref('markdown')
const targetChunk = computed(() =>
  contentState.value.lines.find((chunk) => chunk.id === props.chunkId)
)
const locationNotice = computed(() => {
  if (props.referenceSource && contentState.value.loaded) {
    const source = props.referenceSource
    if (source.generation && source.generation !== file.value?.active_generation) {
      return '文档已更新，原引用行号已失效。'
    }
    if (!source.generation) {
      const lines = contentState.value.content.split(/\r\n|\r|\n/)
      const window = lines.slice(source.start_line - 1, source.end_line).join('\n')
      if (!source.numbered || window !== source.content) return '原引用位置无法核验，请查看检索片段。'
    }
  }
  if (contentState.value.lineLocation?.available !== false) return ''
  const reasons = {
    document_not_indexed: '文档尚未完成入库，完成入库后重新检索可定位高亮。',
    index_changed: '文档索引已更新，请重新检索以定位原文。',
    chunks_unavailable: '片段信息加载失败，暂时无法定位原文。',
    content_unavailable: '完整解析原文不可用，暂时无法定位高亮。'
  }
  return reasons[contentState.value.lineLocation.reason] || '暂时无法定位原文。'
})
const targetLines = computed(() =>
  props.chunkId
    ? {
        start: locationNotice.value ? null : targetChunk.value?.start_line || null,
        end: locationNotice.value ? null : targetChunk.value?.end_line || null
      }
    : { start: locationNotice.value ? null : props.startLine, end: locationNotice.value ? null : props.endLine }
)
const hasContent = computed(
  () =>
    (contentState.value.lines && contentState.value.lines.length > 0) || contentState.value.content
)
const sourcePreviewCandidateType = computed(() => getPreviewTypeByPath(file.value?.filename || ''))
const sourcePreviewDisplayType = computed(
  () => sourcePreview.value.type || sourcePreviewCandidateType.value
)
const sourceContentLength = computed(() =>
  typeof sourcePreview.value.content === 'string' ? sourcePreview.value.content.length : 0
)
const sourcePreviewFile = computed(() => {
  if (!file.value) return null
  const isError = Boolean(
    sourcePreview.value.message &&
    !sourcePreview.value.supported &&
    !sourcePreview.value.content &&
    !sourcePreview.value.url
  )
  return {
    ...file.value,
    content: sourcePreview.value.content,
    previewType: sourcePreviewDisplayType.value,
    previewUrl: sourcePreview.value.url,
    supported: sourcePreview.value.supported,
    status: sourcePreview.value.loading
      ? 'loading'
      : isError
        ? 'error'
        : sourcePreview.value.supported === false
          ? 'unsupported'
          : 'ready',
    errorMessage: sourcePreview.value.message,
    loadingMessage: '正在加载文件内容...',
    message: sourcePreview.value.message
  }
})
const hasSourcePreview = computed(() => canPreviewOriginal(file.value))
const hasMarkdownPreview = computed(() => canPreviewParsed(file.value) || hasContent.value)
const hasChunkPreview = computed(() => canPreviewChunks(file.value))
const availableViewModes = computed(() => {
  const modes = []
  if (hasSourcePreview.value) modes.push('source')
  if (hasMarkdownPreview.value) modes.push('markdown', 'markdown-source')
  if (hasChunkPreview.value) modes.push('chunks')
  return modes
})
const hasAvailableView = computed(() => availableViewModes.value.length > 0)

const viewModeOptions = computed(() => {
  const optionMap = {
    source: { title: '源文件', value: 'source', icon: FileSearch },
    markdown: { title: 'Markdown 预览', value: 'markdown', icon: FileText },
    'markdown-source': { title: 'Markdown 源码', value: 'markdown-source', icon: Code2 },
    chunks: { title: 'Chunks', value: 'chunks', icon: Rows3 }
  }
  return availableViewModes.value.map((mode) => optionMap[mode])
})
const currentViewOption = computed(() => viewModeOptions.value.find((option) => option.value === viewMode.value))

const loadBasicInfo = async () => {
  const kbId = String(props.kbId || '')
  const fileId = String(props.fileId || '')
  if (!kbId || !fileId) return

  const editRequested = props.editRequested
  if (editRequested) emit('update:editRequested', false)
  resetLocalState()
  const requestId = ++basicRequestSeq
  basicLoading.value = true

  try {
    const data = await documentApi.getDocumentBasicInfo(kbId, fileId)
    if (requestId !== basicRequestSeq) return
    ensureApiSuccess(data, '加载文件信息失败')

    const nextFile = normalizeFileMeta(data?.meta || data)
    if (nextFile.is_folder) {
      detailError.value = '文件夹不支持详情预览'
      return
    }

    file.value = nextFile
    viewMode.value =
      (props.startLine || props.chunkId || props.embedded) && canPreviewParsed(nextFile)
        ? 'markdown'
        : getDefaultDetailView(nextFile)
    if (editRequested) await startMarkdownEdit()
  } catch (error) {
    if (requestId !== basicRequestSeq) return
    console.error('加载文件基本信息失败:', error)
    detailError.value = error.message || '加载文件信息失败'
    message.error(detailError.value)
  } finally {
    if (requestId === basicRequestSeq) {
      basicLoading.value = false
    }
  }
}

const loadParsedContent = async () => {
  if (!props.kbId || !props.fileId || contentState.value.loading || contentState.value.loaded)
    return

  const requestId = ++contentRequestSeq
  contentState.value = {
    ...contentState.value,
    loading: true,
    error: ''
  }

  try {
    const data = await documentApi.getDocumentContent(props.kbId, props.fileId)
    if (requestId !== contentRequestSeq) return
    ensureApiSuccess(data, '加载解析内容失败')
    contentState.value = {
      loading: false,
      loaded: true,
      lines: data?.lines || [],
      content: data?.content || '',
      lineLocation: data?.line_location || null,
      error: ''
    }
  } catch (error) {
    if (requestId !== contentRequestSeq) return
    console.error('加载解析内容失败:', error)
    const errorMessage = error.message || '加载解析内容失败'
    contentState.value = {
      loading: false,
      loaded: false,
      lines: [],
      content: '',
      lineLocation: null,
      error: errorMessage
    }
    message.error(errorMessage)
  }
}

watch(
  () => [props.open, props.kbId, props.fileId, userStore.token, props.editable],
  ([open]) => {
    fileActionsOpen.value = false
    compactActionsOpen.value = false
    if (!open) {
      resetLocalState()
      return
    }
    loadBasicInfo()
  },
  { immediate: true }
)

watch(
  availableViewModes,
  (modes) => {
    if (modes.length > 0 && !modes.includes(viewMode.value)) {
      viewMode.value = modes[0]
    }
  },
  { immediate: true }
)

watch(
  [visible, file, viewMode, () => markdownEditor.value.active],
  async ([open, currentFile, currentViewMode]) => {
    if (!open || !currentFile || markdownEditor.value.active) return
    if (
      (['markdown', 'markdown-source'].includes(currentViewMode) &&
        canPreviewParsed(currentFile)) ||
      (currentViewMode === 'chunks' && canPreviewChunks(currentFile))
    ) {
      await loadParsedContent()
    }
  },
  { immediate: true }
)

watch(
  [visible, file, viewMode, () => markdownEditor.value.active],
  async ([open, currentFile, currentViewMode]) => {
    if (!open || !currentFile || markdownEditor.value.active || !hasSourcePreview.value || currentViewMode !== 'source') {
      if (!open || !hasSourcePreview.value) {
        resetSourcePreview()
      }
      return
    }

    await loadSourcePreview()
  },
  { immediate: true }
)

// 统计信息
const mergeResult = computed(() => mergeChunks(contentState.value.lines || []))
const mergedContent = computed(
  () =>
    contentState.value.content ||
    (!props.startLine && !props.chunkId ? mergeResult.value.content : '') ||
    ''
)
const charCount = computed(() => mergedContent.value.length)
const chunkCount = computed(
  () => contentState.value.lines?.length || 0
)
const viewInfoText = computed(() => {
  if (viewMode.value === 'chunks') {
    if (contentState.value.loading) return ''
    return `${chunkCount.value} 个片段`
  }
  if (viewMode.value === 'source') {
    if (sourcePreview.value.loading) return ''
    if (sourceContentLength.value > 0) return `${formatTextLength(sourceContentLength.value)} 字符`
    if (sourcePreview.value.url) return '源文件预览'
    return ''
  }
  if (contentState.value.loading) return ''
  return `${formatTextLength(charCount.value)} 字符`
})

// 格式化文本长度
function formatTextLength(length) {
  if (!length && length !== 0) return '0 字符'

  if (length < 1000) {
    return `${length}`
  } else {
    return `${(length / 1000).toFixed(1)}k`
  }
}

const afterOpenChange = (open) => {
  if (!open && !props.open) {
    resetLocalState()
    emit('closed')
  }
}

const loadSourcePreview = async () => {
  if (!file.value?.file_id || !props.kbId || !props.fileId || !hasSourcePreview.value) return
  if (sourcePreview.value.url || sourcePreview.value.content || sourcePreview.value.message) return

  const requestId = ++sourceRequestSeq
  sourcePreview.value.loading = true
  try {
    const response = await getWorkspaceKnowledgeFileContent(props.kbId, props.fileId)
    const preview = await normalizePreviewResponse(response)
    if (requestId !== sourceRequestSeq) {
      if (preview.previewUrl) {
        window.URL.revokeObjectURL(preview.previewUrl)
      }
      return
    }
    revokeSourcePreviewUrl()
    sourcePreview.value.type = preview.previewType || sourcePreviewCandidateType.value
    sourcePreview.value.message = preview.message || ''
    sourcePreview.value.supported = preview.supported !== false
    sourcePreview.value.url = preview.previewUrl || ''
    sourcePreview.value.content = preview.content || ''
  } catch (error) {
    if (requestId !== sourceRequestSeq) return
    console.error('加载源文件预览失败:', error)
    sourcePreview.value.message = error.message || '加载源文件预览失败'
    sourcePreview.value.supported = false
    message.error(sourcePreview.value.message)
  } finally {
    if (requestId === sourceRequestSeq) {
      sourcePreview.value.loading = false
    }
  }
}

// 下载菜单点击处理
const handleDownloadMenuClick = ({ key }) => {
  if (key === 'original') {
    handleDownloadOriginal()
  } else if (key === 'markdown') {
    handleDownloadMarkdown()
  }
}

// 下载原文
const handleDownloadOriginal = async () => {
  if (!file.value || !props.kbId || !props.fileId) {
    message.error('文件信息不完整')
    return
  }

  downloadingOriginal.value = true
  try {
    const response = await documentApi.downloadDocument(props.kbId, props.fileId)

    const contentDisposition = response.headers.get('content-disposition')
    const filename = parseDownloadFilename(contentDisposition) || file.value.filename
    // 创建blob并下载
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    message.success('下载成功')
  } catch (error) {
    console.error('下载文件时出错:', error)
    message.error(error.message || '下载文件失败')
  } finally {
    downloadingOriginal.value = false
  }
}

// 下载 Markdown
const handleDownloadMarkdown = () => {
  const content = mergedContent.value

  if (!content) {
    message.error('没有可下载的 Markdown 内容')
    return
  }

  downloadingMarkdown.value = true
  try {
    // 生成文件名（如果原文件没有 .md 扩展名，则添加）
    let filename = file.value.filename || 'document.md'
    if (!filename.toLowerCase().endsWith('.md')) {
      // 移除原扩展名，添加 .md
      const lastDotIndex = filename.lastIndexOf('.')
      if (lastDotIndex > 0) {
        filename = filename.substring(0, lastDotIndex) + '.md'
      } else {
        filename = filename + '.md'
      }
    }

    // 创建 blob 并下载
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    message.success('下载成功')
  } catch (error) {
    console.error('下载 Markdown 时出错:', error)
    message.error(error.message || '下载 Markdown 失败')
  } finally {
    downloadingMarkdown.value = false
  }
}

watch([() => props.startLine, () => props.endLine, () => props.chunkId], () => {
  if (
    (props.startLine || props.chunkId) &&
    !['markdown', 'markdown-source'].includes(viewMode.value)
  ) {
    viewMode.value = 'markdown'
  }
})

/** 刷新或离开页面前提醒尚未确认保存的草稿。 */
const protectMarkdownUnload = (event) => {
  if (!markdownExitRequired.value) return
  event.preventDefault()
  event.returnValue = ''
}
window.addEventListener('beforeunload', protectMarkdownUnload)
/** 离页与同一路由切库共用退出保护，明确放弃后才允许导航。 */
const confirmMarkdownNavigation = () => {
  if (!markdownExitRequired.value) return true
  return new Promise(resolve => {
    markdownExitAction = () => resolve(true)
    markdownExitOpen.value = true
    const stop = watch(markdownExitOpen, (open) => {
      if (!open) { stop(); resolve(false) }
    }, { flush: 'post' })
  })
}
/** 同库的视图区切换保留编辑器，只有更换知识库才需要放弃确认。 */
const confirmMarkdownRouteUpdate = (to, from) =>
  to.params.kbId === from.params.kbId ? true : confirmMarkdownNavigation()
onBeforeRouteLeave(confirmMarkdownNavigation)
onBeforeRouteUpdate(confirmMarkdownRouteUpdate)
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', protectMarkdownUnload)
  resetLocalState()
})
</script>

<style scoped>
.markdown-editor-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  gap: 12px;
  padding: 16px 24px;
}
.markdown-editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.markdown-editor-view,
.markdown-editor-actions { display: flex; align-items: center; gap: 12px; }
.markdown-editor-view { min-width: 0; }
.markdown-editor-view :deep(.ant-radio-button-wrapper-checked) {
  background: var(--gray-100);
  color: var(--color-text);
  border-color: var(--gray-200);
  font-weight: 600;
}
.markdown-editor-status { color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.markdown-editor-hint { margin: 0; color: var(--color-text-secondary); font-size: 13px; }
.markdown-editor-panel :deep(.agent-file-preview) { flex: 1; min-height: 0; }
.markdown-editor-panel :deep(.file-content) { min-height: 0; }
.markdown-draft-preview { flex: 1; min-height: 0; overflow: auto; border: 1px solid var(--gray-150); border-radius: 8px; }
.latest-markdown-preview { height: 48vh; overflow: auto; border: 1px solid var(--gray-150); border-radius: 8px; }
.markdown-editor-panel :deep(.ant-alert) { flex-shrink: 0; }
@media (max-width: 800px) {
  .markdown-editor-panel { padding: 12px; }
  .markdown-editor-toolbar { flex-wrap: wrap; gap: 8px; }
  .markdown-editor-actions { margin-left: auto; }
}

.location-notice {
  margin: 12px 16px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.file-detail-embedded {
  container: knowledge-file-preview / inline-size;
  height: 100%;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  overflow: hidden;
}
.file-detail-embedded .modal-title-wrapper {
  flex-wrap: nowrap;
}
.modal-title-wrapper {
  flex-shrink: 0;
  padding: 6px 16px;
  border-bottom: 1px solid var(--gray-150);
}

.file-detail-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.content-panel,
.chunks-panel {
  flex: 1;
  overflow-y: auto;
  padding: 0;
  min-height: 0;
}

.chunks-panel {
  overflow: hidden;
}

.source-panel {
  overflow: hidden;
}

:deep(.source-preview-container) {
  height: 100%;
  max-height: none;
}

:deep(.source-preview-content) {
  flex: 1 1 auto;
  max-height: none;
  min-height: 0;
}

:deep(.source-preview-content .html-preview),
:deep(.source-preview-content .pdf-preview) {
  display: block;
  height: 100%;
  min-height: 100%;
}

.markdown-content {
  min-height: 100%;
}

.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

.empty-content {
  text-align: center;
  padding: 40px 0;
  color: var(--gray-400);
  width: 100%;
}

</style>

<style lang="less">
.file-detail {
  .ant-modal {
    top: 20px;
  }

  .ant-modal-content {
    padding: 12px;
  }

  .ant-modal-header {
    .ant-modal-title {
      width: 100%;
    }
  }
}

.modal-title-wrapper {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-width: 0;
}

/* 文件标题样式 */
.file-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;

  svg {
    flex: 0 0 auto;
  }
}

.file-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 15px;
  color: var(--gray-900);
}

.title-info {
  font-size: 13px;
  color: var(--gray-600);
  font-weight: 500;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  margin-left: auto;
  min-width: 0;
}

.header-controls .compact-controls {
  display: none;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 32px;
  height: 28px;
  padding: 0;
}

.file-detail .ant-modal-body {
  container: knowledge-file-preview / inline-size;
}

@container knowledge-file-preview (max-width: 640px) {
  .header-controls .expanded-controls {
    display: none;
  }

  .header-controls .compact-controls {
    display: inline-flex;
  }
}

/* 自定义关闭按钮 */
.custom-close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  color: var(--gray-500);
  transition: all 0.2s;

  &:hover {
    background: var(--gray-100);
    color: var(--gray-700);
  }
}

/* 视图切换控件 */
.view-controls {
  flex: 0 0 auto;
  height: 28px;
  padding: 0 10px;
}

.view-info {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--gray-500);
  white-space: nowrap;
}

/* 文件操作使用中性色区分当前视图与悬停，避免组件库推导出偏灰绿的主色底。 */
.ant-dropdown.knowledge-file-actions .ant-dropdown-menu {
  border: 1px solid var(--gray-150);
  background: var(--gray-0);
  box-shadow: var(--shadow-popover);

  .ant-dropdown-menu-item {
    color: var(--gray-800);
  }

  .ant-dropdown-menu-item-divider {
    background: var(--gray-200);
  }

  .ant-dropdown-menu-item:hover,
  .ant-dropdown-menu-item-active {
    background: var(--gray-50);
  }

  .ant-dropdown-menu-item-selected,
  .ant-dropdown-menu-item-selected:hover {
    background: var(--gray-100);
    color: var(--gray-900);
    font-weight: 500;
  }

  .ant-dropdown-menu-item-disabled,
  .ant-dropdown-menu-item-disabled:hover {
    background: transparent;
    color: var(--gray-400);
  }
}

.dark {
  .file-detail-embedded {
    background: var(--gray-25);
    border-color: var(--gray-150);
  }

  .file-detail .modal-title-wrapper,
  .file-detail-embedded .modal-title-wrapper {
    border-bottom-color: var(--gray-150);
  }

  .file-detail .view-controls,
  .file-detail-embedded .view-controls {
    background: var(--gray-50);
    border-color: transparent;

    &:hover {
      background: var(--gray-100);
    }
  }

  .ant-dropdown.knowledge-file-actions .ant-dropdown-menu {
    background: var(--gray-30);
    border-color: var(--gray-200);

    .ant-dropdown-menu-item-divider {
      background: var(--gray-300);
    }
  }
}

/* 下拉菜单样式 */
.ant-dropdown-menu {
  border-radius: 8px;
  padding: 4px;
}

.ant-dropdown-menu-item {
  border-radius: 6px;
  display: flex;
  align-items: center;
  padding: 8px 12px;

  // svg {
  //   margin-right: 8px;
  // }
}
</style>
