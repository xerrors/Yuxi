<template>
  <a-modal
    :open="open"
    :title="fileIds.length > 1 ? `批量修改属性 · ${fileIds.length} 个文件` : '文件属性'"
    width="min(620px, calc(100vw - 32px))"
    ok-text="保存属性"
    cancel-text="取消"
    :confirm-loading="saving"
    :ok-button-props="{ disabled: loading || !fields.length || Boolean(loadError) || !hasChanges }"
    :mask-closable="!saving"
    @cancel="close"
    @ok="save"
  >
    <a-alert
      v-if="error || loadError"
      type="error"
      show-icon
      :message="error || loadError"
      role="alert"
    />
    <a-spin v-if="loading" tip="正在读取属性…" />
    <a-empty
      v-else-if="!fields.length && !loadError"
      description="当前知识库尚未定义属性，请先在知识库配置中添加字段。"
    />
    <FileAttributesForm
      v-else-if="!loadError"
      :fields="fields"
      v-model:values="values"
      v-model:operations="operations"
      :disabled="saving"
    />
    <a-button v-if="loadError" @click="load">重试读取</a-button>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { documentApi, metadataApi } from '@/apis/knowledge_api'
import FileAttributesForm from './FileAttributesForm.vue'
import { buildAttributePatch } from '../model/metadata'

const props = defineProps({
  open: Boolean,
  kbId: { type: String, required: true },
  fileIds: { type: Array, required: true }
})
const emit = defineEmits(['update:open', 'saved'])
const fields = ref([])
const values = ref({})
const operations = ref({})
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const loadError = ref('')
const hasChanges = computed(() => Object.values(operations.value).some((value) => value !== 'keep'))
let request = 0

/** 每次打开重新读取，关闭后到达的结果不覆盖下次编辑。 */
async function load() {
  const current = ++request
  loading.value = true
  loadError.value = ''
  error.value = ''
  operations.value = {}
  values.value = {}
  try {
    const [schema, file] = await Promise.all([
      metadataApi.getFields(props.kbId),
      props.fileIds.length === 1
        ? documentApi.getDocumentBasicInfo(props.kbId, props.fileIds[0])
        : Promise.resolve(null)
    ])
    if (current !== request) return
    fields.value = schema.fields
    values.value = { ...(file?.meta?.attributes || {}) }
  } catch (reason) {
    if (current === request) loadError.value = reason.message || '读取属性失败'
  } finally {
    if (current === request) loading.value = false
  }
}

async function save() {
  if (saving.value || loading.value) return
  error.value = ''
  try {
    const changes = buildAttributePatch(fields.value, operations.value, values.value)
    saving.value = true
    const result = await metadataApi.patchFiles(props.kbId, {
      file_ids: [...props.fileIds],
      ...changes
    })
    message.success('文件属性已保存')
    emit('saved', result.files)
    emit('update:open', false)
  } catch (reason) {
    error.value = reason.message || '保存失败，请检查字段值后重试'
  } finally {
    saving.value = false
  }
}

function close() {
  if (saving.value) return
  request++
  emit('update:open', false)
}
watch(
  () => [props.open, props.kbId, props.fileIds.join(',')],
  () => {
    if (props.open) load()
    else request++
  },
  { immediate: true }
)
</script>
