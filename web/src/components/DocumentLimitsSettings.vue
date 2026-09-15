<template>
  <section class="document-limits-settings" aria-labelledby="document-limits-title">
    <h3 id="document-limits-title">文档处理限制</h3>
    <p>作用于知识库文档上传及对应 OCR；聊天附件和第三方引擎仍按各自限制执行。</p>
    <a-spin :spinning="loading">
      <a-alert v-if="error" :message="error" type="error" show-icon />
      <a-alert v-if="notice" :message="notice" type="success" show-icon />
      <a-form layout="vertical">
        <div class="limits-fields">
          <a-form-item label="单文件上传上限（MiB）">
            <a-input-number
              v-model:value="uploadMaxMib"
              :min="1"
              :max="settings?.hard_upload_max_mib"
              :step="1"
              :disabled="busy || !settings"
              aria-label="单文件上传上限（MiB）"
            />
            <small v-if="settings"
              >允许范围：1–{{ settings.hard_upload_max_mib }} MiB（部署硬上限）</small
            >
          </a-form-item>
          <a-form-item label="OCR 最大页数（页）">
            <a-input-number
              v-model:value="ocrMaxPages"
              :min="1"
              :max="settings?.hard_ocr_max_pages"
              :step="1"
              :disabled="busy || !settings"
              aria-label="OCR 最大页数（页）"
            />
            <small v-if="settings"
              >允许范围：1–{{ settings.hard_ocr_max_pages }} 页（部署硬上限）</small
            >
          </a-form-item>
        </div>
        <p v-if="settings">
          当前实际上传上限：{{ settings.effective_upload_max_bytes / 1024 / 1024 }} MiB；当前 OCR
          上限：{{ settings.ocr_max_pages }} 页。版本：{{ settings.revision }}。
        </p>
        <p>
          保存后新接受的任务采用新配置；已经接受的任务保留原限额快照。超出部署硬上限需先调整部署能力。
        </p>
      </a-form>
      <div class="settings-actions" role="group" aria-label="文档限制操作">
        <a-button
          type="primary"
          html-type="button"
          @click="save"
          :loading="saving"
          :disabled="busy || !valid"
          >保存文档限制</a-button
        >
        <a-button :disabled="busy" @click="load">重新加载</a-button>
        <a-popconfirm title="恢复服务端部署默认文档限制？" @confirm="reset">
          <a-button :disabled="busy || !settings" :loading="resetting">恢复默认限制</a-button>
        </a-popconfirm>
      </div>
      <p v-if="settings">
        部署默认：{{ settings.defaults.upload_max_mib }} MiB /
        {{ settings.defaults.ocr_max_pages }} 页。1 MiB = 1,048,576 字节。
      </p>
    </a-spin>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { documentLimitsApi } from '@/apis/system_api'

const settings = ref(null)
const uploadMaxMib = ref(null)
const ocrMaxPages = ref(null)
const loading = ref(false)
const saving = ref(false)
const resetting = ref(false)
const error = ref('')
const notice = ref('')
const busy = computed(() => loading.value || saving.value || resetting.value)
const valid = computed(
  () =>
    settings.value &&
    Number.isInteger(uploadMaxMib.value) &&
    uploadMaxMib.value >= 1 &&
    uploadMaxMib.value <= settings.value.hard_upload_max_mib &&
    Number.isInteger(ocrMaxPages.value) &&
    ocrMaxPages.value >= 1 &&
    ocrMaxPages.value <= settings.value.hard_ocr_max_pages
)
function apply(result) {
  settings.value = result
  uploadMaxMib.value = result.upload_max_mib
  ocrMaxPages.value = result.ocr_max_pages
}
function failure(err) {
  error.value =
    err.status === 409
      ? '配置已被更新，请重新加载后再保存'
      : err.message || '文档限制操作失败，请重试'
}
async function load() {
  loading.value = true
  error.value = ''
  notice.value = ''
  try {
    apply(await documentLimitsApi.get())
  } catch (err) {
    failure(err)
  } finally {
    loading.value = false
  }
}
async function update(action, flag) {
  if (busy.value || !settings.value) return
  flag.value = true
  error.value = ''
  notice.value = ''
  try {
    apply(await action())
    notice.value = '文档处理限制已保存，新任务按新配置执行'
  } catch (err) {
    failure(err)
  } finally {
    flag.value = false
  }
}
function save() {
  if (!valid.value) return
  return update(
    () =>
      documentLimitsApi.update({
        revision: settings.value.revision,
        upload_max_mib: uploadMaxMib.value,
        ocr_max_pages: ocrMaxPages.value
      }),
    saving
  )
}
function reset() {
  return update(() => documentLimitsApi.reset(settings.value.revision), resetting)
}
onMounted(load)
</script>

<style scoped>
.settings-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--gray-200);
}

.document-limits-settings {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-50);
}
h3 {
  margin-top: 0;
}
p,
small {
  color: var(--gray-600);
  font-size: 12px;
}
small {
  display: block;
  margin-top: 4px;
}
.limits-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}
.limits-fields > * {
  flex: 1;
  min-width: 180px;
}
</style>
