<template>
  <section class="knowledge-media-panel" aria-label="图片与音视频索引" :aria-busy="busy || loading">
    <strong v-if="sourceFileId">{{ filename || selectedFile?.name || '媒体文件' }}</strong>
    <a-spin v-if="loading" tip="正在读取媒体配置…" />
    <a-alert v-if="error" type="error" show-icon :message="error" />
    <a-alert
      v-if="modelError"
      type="error"
      show-icon
      :message="modelError"
      :description="modelErrorDetails"
    >
      <template #action>
        <a-button size="small" :disabled="loading" @click="load">重试</a-button>
      </template>
    </a-alert>
    <a-alert v-if="documentImageSettingsError" type="error" show-icon :message="documentImageSettingsError">
      <template #action>
        <a-button size="small" :disabled="loading" @click="load">重试</a-button>
      </template>
    </a-alert>
    <a-alert
      v-if="job?.status === 'failed' || job?.status === 'cancelled'"
      type="error"
      show-icon
      :message="
        job.status === 'cancelled'
          ? '媒体索引已取消。'
          : '媒体索引未完成，请检查模型与文件格式后重试。'
      "
      description="可在后台任务查看失败信息；文件格式有误时，请重新上传原件。"
    />
    <div v-if="job && ['pending', 'running'].includes(job.status)" class="media-job" role="status">
      <a-progress :percent="Math.round(job.progress || 0)" :show-info="false" />
      <span>{{
        job.status === 'pending' ? '媒体索引已排队' : job.message || '正在建立媒体索引…'
      }}</span>
      <a-button size="small" @click="jobs.openDrawer()">查看后台任务</a-button>
    </div>
    <p v-if="current" class="media-current" role="status">
      媒体索引可用 · {{ current.asset_count }} 个资产<span v-if="current.duration_ms">
        · {{ Math.ceil(current.duration_ms / 1000) }} 秒</span
      >
    </p>
    <p v-else-if="sourceFileId && !loading && !activeJob" class="media-hint">
      尚无可用媒体索引。选择模型后可建立索引。
    </p>
    <div v-if="editable" class="media-models">
      <div class="media-models-heading">
        <strong>多模态模型设置</strong>
        <p class="media-hint">这些选项位于图片与音视频上传表单中；模型目录只列出已启用且兼容的模型。</p>
      </div>
      <div v-for="field in fields" :key="field.key" class="media-model-field">
        <label :for="`${controlId}-${field.key}`">{{ field.label }}</label>
        <a-select
          :id="`${controlId}-${field.key}`"
          v-model:value="config[field.key]"
          :options="models?.[field.key] || []"
          :disabled="busy || activeJob || loading || !models"
          :placeholder="!models ? '模型目录不可用' : models[field.key]?.length ? '请选择已启用模型' : '暂无兼容模型'"
          allow-clear
          :open="openModel === field.key"
          @dropdown-visible-change="
            (open) => {
              openModel = open ? field.key : ''
            }
          "
          @keydown.esc.capture="closeModelDropdown"
        />
        <span class="media-hint">{{ field.description }}</span>
        <span v-if="!models && !modelError" class="media-hint">正在读取可用模型。</span>
        <span v-else-if="models && !models[field.key]?.length" class="media-hint">
          请在模型设置中启用兼容模型。
        </span>
      </div>
    </div>
    <a-upload-dragger
      v-if="!sourceFileId"
      :file-list="fileList"
      :multiple="false"
      :max-count="1"
      :accept="acceptedTypes"
      :before-upload="selectFile"
      :disabled="busy"
      @remove="removeFile"
    >
      <p class="ant-upload-text">选择图片、录音或视频</p>
      <p class="ant-upload-hint">每次一个文件，最多 100 MiB；音视频最长 10 分钟。</p>
      <p class="ant-upload-hint">PNG、JPEG、WebP、WAV、MP3、FLAC、OGG、MP4、MOV、MKV、WebM</p>
    </a-upload-dragger>
    <section v-if="editable" class="document-image-settings" aria-label="文档图片自动索引">
      <div class="document-image-settings-heading">
        <div>
          <strong>解析文档图片自动索引</strong>
          <p class="media-hint">此设置作用于整个知识库。开启后，成功解析文档时会为正文引用的图片建立独立视觉索引。</p>
        </div>
        <a-switch
          :checked="documentImageSettings.enabled"
          :disabled="loading || settingsSaving || !documentImageSettingsLoaded || (!documentImageSettings.enabled && !models?.embedding_model?.length)"
          aria-label="解析文档图片自动索引"
          @change="setDocumentImageAutoIndexEnabled"
        />
      </div>
      <div v-if="documentImageSettings.enabled" class="media-model-field">
        <label :for="`${controlId}-document-image-embedding`">文档图片嵌入模型</label>
        <a-select
          :id="`${controlId}-document-image-embedding`"
          v-model:value="documentImageSettings.embedding_model"
          :options="models?.embedding_model || []"
          :disabled="loading || settingsSaving || !models?.embedding_model?.length"
          :placeholder="models?.embedding_model?.length ? '请选择已启用模型' : '暂无兼容图像嵌入模型'"
          allow-clear
        />
        <span v-if="!models?.embedding_model?.length" class="media-hint">请先在模型设置中启用兼容的图像嵌入模型。</span>
      </div>
      <div class="document-image-settings-actions">
        <span v-if="documentImageSettingsMessage" class="media-hint" role="status">{{ documentImageSettingsMessage }}</span>
        <a-button
          size="small"
          :loading="settingsSaving"
          :disabled="!canSaveDocumentImageSettings"
          @click="saveDocumentImageAutoIndexSettings"
        >保存设置</a-button>
      </div>
    </section>
    <p
      v-if="editable && (sourceFileId || selectedFile) && !canSubmit && !busy && !activeJob"
      class="media-hint"
      role="status"
    >
      {{ modelError ? '媒体模型目录未就绪，当前不能提交。' : '请为当前媒体选择所有必需的兼容模型。' }}
    </p>
    <div class="media-actions">
      <a-button
        v-if="editable"
        type="primary"
        :loading="busy"
        :disabled="!canSubmit"
        @click="submit"
        >{{ sourceFileId ? '建立媒体索引' : '上传并建立媒体索引' }}</a-button
      >
      <a-button v-if="sourceFileId || error" :disabled="busy" @click="load">刷新状态</a-button>
      <a-button v-if="job" :disabled="busy" @click="jobs.openDrawer()">后台任务</a-button>
    </div>
    <a-alert
      v-if="jobs.lastError && activeJob"
      type="warning"
      show-icon
      message="暂时无法确认后台状态，请刷新状态；任务不会因关闭窗口而取消。"
    />
    <MediaEvidenceReader v-if="assets.length" :assets="assets" :title="filename" />
    <a-button
      v-if="nextOffset !== null"
      :disabled="loading"
      :loading="loadingMore"
      @click="loadMore"
      >加载更多片段</a-button
    >
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, useId, watch } from 'vue'
import { Upload } from 'ant-design-vue'
import { knowledgeMediaApi } from '@/apis/knowledge_media_api'
import { useBackgroundJobsStore } from '@/modules/background-jobs'
import { useUserStore } from '@/modules/identity'
import MediaEvidenceReader from './MediaEvidenceReader.vue'

const props = defineProps({
  kbId: { type: String, required: true },
  fileId: { type: String, default: '' },
  filename: { type: String, default: '' },
  parentId: { type: String, default: null },
  editable: { type: Boolean, default: true }
})
const emit = defineEmits(['busy-change', 'registered'])
const jobs = useBackgroundJobsStore()
const user = useUserStore()
const controlId = useId()
const acceptedTypes = '.png,.jpg,.jpeg,.webp,.wav,.mp3,.flac,.ogg,.m4a,.mp4,.mov,.mkv,.webm'
const fields = [
  {
    key: 'embedding_model',
    label: '图像与视频检索模型',
    description: '图片和视频画面向量检索必需；音频原件可在未配置时保存。'
  },
  {
    key: 'rerank_model',
    label: '画面重排模型',
    description: '可选，用于对检索到的候选画面重新排序，不影响索引创建。'
  },
  {
    key: 'understanding_model',
    label: '图片与音视频核对模型',
    description: '用于核对媒体内容；音频必需，视频需预先选择，后台会核对实际音轨。'
  }
]
const sourceFileId = ref(props.fileId)
const selectedFile = ref(null)
const fileList = computed(() =>
  selectedFile.value
    ? [
        {
          uid: selectedFile.value.uid || 'media-file',
          name: selectedFile.value.name,
          originFileObj: selectedFile.value
        }
      ]
    : []
)
const models = ref(null)
const modelError = ref('')
const modelErrorDetails = ref('')
const config = ref({
  embedding_model: undefined,
  rerank_model: undefined,
  understanding_model: undefined
})
const documentImageSettings = ref({ enabled: false, embedding_model: undefined })
const savedDocumentImageSettings = ref(null)
const documentImageSettingsLoaded = ref(false)
const documentImageSettingsError = ref('')
const documentImageSettingsMessage = ref('')
const settingsSaving = ref(false)
const current = ref(null)
const job = ref(null)
const assets = ref([])
const nextOffset = ref(null)
const loadingMore = ref(false)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const openModel = ref('')
let generation = 0
let controller
const activeJob = computed(() => ['pending', 'running'].includes(job.value?.status))
const canSaveDocumentImageSettings = computed(() => {
  const currentSettings = documentImageSettings.value
  return (
    documentImageSettingsLoaded.value &&
    !loading.value &&
    !settingsSaving.value &&
    JSON.stringify(currentSettings) !== JSON.stringify(savedDocumentImageSettings.value) &&
    (!currentSettings.enabled ||
      models.value?.embedding_model?.some((item) => item.value === currentSettings.embedding_model))
  )
})
const selectedMediaName = computed(() => selectedFile.value?.name || props.filename)
const audioOnly = computed(() => /\.(wav|mp3|flac|ogg|m4a)$/i.test(selectedMediaName.value))
const documentImages = computed(() =>
  !!props.fileId && !/\.(png|jpe?g|webp|wav|mp3|flac|ogg|m4a|mp4|mov|mkv|webm)$/i.test(props.filename || '')
)
const videoFile = computed(() => /\.(mp4|mov|mkv|webm)$/i.test(selectedMediaName.value))
const canSubmit = computed(() => {
  if (busy.value || loading.value || activeJob.value || !(sourceFileId.value || selectedFile.value)) return false
  const hasUnderstandingModel =
    models.value?.understanding_model?.some((item) => item.value === config.value.understanding_model) || false
  if (audioOnly.value) return hasUnderstandingModel
  const hasEmbeddingModel =
    models.value?.embedding_model?.some((item) => item.value === config.value.embedding_model) || false
  return hasEmbeddingModel && (!videoFile.value || hasUnderstandingModel)
})

/** 选择器只做即时反馈，真实格式仍由后台解码核对。 */
function selectFile(file) {
  error.value = ''
  const extension = `.${file.name.split('.').pop().toLowerCase()}`
  if (!acceptedTypes.split(',').includes(extension)) {
    error.value = '请选择受支持的图片、录音或视频格式。'
    return Upload.LIST_IGNORE
  }
  if (file.size > 100 * 1024 * 1024) {
    error.value = '文件超过 100 MiB，请先裁分媒体。'
    return Upload.LIST_IGNORE
  }
  selectedFile.value = file
  return false
}
/** 上传开始后不允许移除正在提交的原件。 */
function removeFile() {
  if (!busy.value) selectedFile.value = null
  return !busy.value
}
/** Escape 先关闭当前模型选项，保留上传弹窗和输入焦点。 */
function closeModelDropdown(event) {
  if (!openModel.value) return
  event.preventDefault()
  event.stopPropagation()
  openModel.value = ''
}
/** 仅为后端返回的当前资产构造固定管理读取地址。 */
function readableAssets(items) {
  return items.map((asset) => ({
    ...asset,
    read_url: knowledgeMediaApi.assetUrl(
      props.kbId,
      sourceFileId.value,
      asset.asset_id,
      asset.revision
    )
  }))
}
/** 合并当前发布事实和作业观察，晚到响应不得覆盖新文件。 */
async function load() {
  const id = ++generation
  loadingMore.value = false
  loading.value = true
  error.value = ''
  const [catalogueResult, settingsResult, stateResult, listingResult] = await Promise.allSettled([
    props.editable ? knowledgeMediaApi.models(props.kbId) : Promise.resolve(null),
    props.editable ? knowledgeMediaApi.documentImageAutoIndexSettings(props.kbId) : Promise.resolve(null),
    sourceFileId.value
      ? knowledgeMediaApi.status(props.kbId, sourceFileId.value)
      : Promise.resolve(null),
    sourceFileId.value
      ? knowledgeMediaApi.list(props.kbId, sourceFileId.value)
      : Promise.resolve(null)
  ])
  if (id !== generation) return
  const catalogue = catalogueResult.status === 'fulfilled' ? catalogueResult.value : null
  const storedSettings = settingsResult.status === 'fulfilled' ? settingsResult.value?.settings : null
  const state = stateResult.status === 'fulfilled' ? stateResult.value : null
  const listing = listingResult.status === 'fulfilled' ? listingResult.value : null
  models.value = catalogue
  modelError.value =
    catalogueResult.status === 'rejected'
      ? catalogueResult.reason?.message || '媒体模型目录读取失败，请刷新重试。'
      : ''
  modelErrorDetails.value =
    catalogueResult.status === 'rejected'
      ? [
          catalogueResult.reason?.endpoint,
          catalogueResult.reason?.status
            ? `HTTP ${catalogueResult.reason.status}`
            : '接口未返回 HTTP 状态，请检查 API 是否可用',
          '修复后点击“重试”。'
        ]
          .filter(Boolean)
          .join(' · ')
      : ''
  documentImageSettingsLoaded.value =
    props.editable && settingsResult.status === 'fulfilled' && !!storedSettings
  documentImageSettingsError.value =
    props.editable && (settingsResult.status === 'rejected' || !storedSettings)
      ? settingsResult.reason?.message || '文档图片自动索引设置读取失败，请刷新重试。'
      : ''
  if (documentImageSettingsLoaded.value) {
    documentImageSettings.value = {
      enabled: !!storedSettings.enabled,
      embedding_model: storedSettings.embedding_model || undefined
    }
    savedDocumentImageSettings.value = { ...documentImageSettings.value }
    documentImageSettingsMessage.value = ''
  }
  current.value = state?.current || null
  job.value = state?.job || null
  assets.value = readableAssets(listing?.items || [])
  nextOffset.value = listing?.next_offset ?? null
  const stateError = [stateResult, listingResult].find((result) => result.status === 'rejected')
  if (stateError) error.value = stateError.reason?.message || '媒体状态读取失败，请刷新重试。'
  if (catalogue) {
    for (const field of fields) {
      const configured = state?.current?.config?.[field.key]
      const options = catalogue[field.key] || []
      const selected = config.value[field.key]
      if (selected && options.some((item) => item.value === selected)) continue
      if (configured && options.some((item) => item.value === configured)) {
        config.value[field.key] = configured
      } else {
        config.value[field.key] = field.key === 'embedding_model' ? options[0]?.value : undefined
      }
    }
  }
  try {
    if (activeJob.value)
      await jobs.createJobRegistration(() => {
        if (id === generation) void load()
      })({ job_id: job.value.id })
  } catch (cause) {
    if (id === generation) error.value = cause.message || '后台任务状态读取失败。'
  } finally {
    if (id === generation) loading.value = false
  }
}
/** 打开自动索引时优先选中第一个当前兼容的图像嵌入模型。 */
function setDocumentImageAutoIndexEnabled(enabled) {
  documentImageSettings.value.enabled = enabled
  if (enabled && !documentImageSettings.value.embedding_model) {
    documentImageSettings.value.embedding_model = models.value?.embedding_model?.[0]?.value
  }
  documentImageSettingsMessage.value = ''
}
/** 文档图片配置是知识库级设置，保存后从服务端回读最终值。 */
async function saveDocumentImageAutoIndexSettings() {
  if (!canSaveDocumentImageSettings.value) return
  settingsSaving.value = true
  documentImageSettingsError.value = ''
  documentImageSettingsMessage.value = ''
  try {
    const result = await knowledgeMediaApi.updateDocumentImageAutoIndexSettings(props.kbId, {
      enabled: documentImageSettings.value.enabled,
      embedding_model: documentImageSettings.value.embedding_model || null
    })
    const saved = result?.settings
    if (!saved) throw new Error('保存结果未确认，请刷新设置核对。')
    documentImageSettings.value = {
      enabled: !!saved.enabled,
      embedding_model: saved.embedding_model || undefined
    }
    savedDocumentImageSettings.value = { ...documentImageSettings.value }
    documentImageSettingsMessage.value = '设置已保存。'
  } catch (cause) {
    documentImageSettingsError.value = cause.message || '文档图片自动索引设置保存失败。'
  } finally {
    settingsSaving.value = false
  }
}
/** 沿后端游标读取本文件剩余片段，不扩大来源。 */
async function loadMore() {
  if (loading.value || loadingMore.value || nextOffset.value === null) return
  const id = generation
  loadingMore.value = true
  try {
    const listing = await knowledgeMediaApi.list(props.kbId, sourceFileId.value, nextOffset.value)
    if (id !== generation) return
    assets.value.push(...readableAssets(listing.items || []))
    nextOffset.value = listing.next_offset ?? null
  } catch (cause) {
    if (id === generation) error.value = cause.message || '片段读取失败。'
  } finally {
    if (id === generation) loadingMore.value = false
  }
}
/** 原件成功登记后保留文件身份，索引重试不重复上传。 */
async function submit() {
  if (!canSubmit.value) return
  const id = generation
  busy.value = true
  error.value = ''
  controller = new AbortController()
  emit('busy-change', true)
  try {
    if (!sourceFileId.value) {
      const result = await knowledgeMediaApi.upload(
        props.kbId,
        selectedFile.value,
        props.parentId,
        controller.signal
      )
      if (id !== generation) return
      if (result.added !== 1 || !result.items?.[0]?.file_id)
        throw new Error('上传结果未确认，请刷新文件列表核对。')
      sourceFileId.value = result.items[0].file_id
    }
    const result = await knowledgeMediaApi.index(props.kbId, sourceFileId.value, config.value, documentImages.value)
    if (id !== generation) return
    await jobs.createJobRegistration(() => {
      if (id === generation) void load()
    })(result)
    emit('registered')
    await load()
  } catch (cause) {
    if (id === generation && cause.name !== 'AbortError')
      error.value = sourceFileId.value
        ? cause.message || '索引提交失败，可直接重试。'
        : `${cause.message || '上传失败'}；若结果未确认，请先刷新文件列表。`
  } finally {
    busy.value = false
    emit('busy-change', false)
  }
}
watch(
  () => [props.kbId, props.fileId, user.token],
  () => {
    controller?.abort()
    sourceFileId.value = props.fileId
    selectedFile.value = null
    current.value = null
    job.value = null
    assets.value = []
    void load()
  },
  { immediate: true }
)
watch(
  () => jobs.jobs.find((item) => item.id === job.value?.id),
  (value) => {
    if (value) job.value = value
  }
)
onUnmounted(() => {
  generation += 1
  controller?.abort()
})
</script>

<style scoped lang="less">
.knowledge-media-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  padding: 16px;
}
.media-models {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
.media-models-heading {
  display: grid;
  gap: 4px;
}
.media-models-heading strong {
  color: var(--color-text);
  font-size: 13px;
}
.document-image-settings {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--gray-150);
}
.document-image-settings-heading,
.document-image-settings-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.document-image-settings-heading > div {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.document-image-settings-heading strong {
  color: var(--color-text);
  font-size: 13px;
}
.document-image-settings-actions {
  justify-content: flex-end;
}
.media-model-field {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.media-model-field label {
  color: var(--color-text);
  font-size: 13px;
}
.media-model-field :deep(.ant-select) {
  width: 100%;
}
.media-hint {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.media-current {
  margin: 0;
  color: var(--color-text);
  font-size: 13px;
}
.media-actions,
.media-job {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.media-job .ant-progress {
  flex-basis: 100%;
  margin: 0;
}
</style>
