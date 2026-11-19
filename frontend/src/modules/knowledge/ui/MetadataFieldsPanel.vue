<template>
  <section class="metadata-fields" aria-label="文件属性字段管理">
    <div class="metadata-heading">
      <p>为文件定义可选属性。新增字段后，已有文件也可以填写。</p>
      <a-button type="primary" :disabled="loading || fields.length >= 64" @click="editField()"
        >添加字段</a-button
      >
    </div>
    <a-alert v-if="error" type="error" show-icon :message="error" role="alert" />
    <a-spin v-if="loading" tip="正在读取字段…" />
    <a-empty v-else-if="!fields.length" description="尚未定义文件属性" />
    <ul v-else class="metadata-field-list">
      <li v-for="field in fields" :key="field.key">
        <div>
          <strong>{{ field.name }}</strong
          ><a-tag>{{ metadataTypeNames[field.type] }}</a-tag>
          <p v-if="field.description">{{ field.description }}</p>
        </div>
        <div class="field-actions">
          <a-button type="link" :aria-label="`编辑字段 ${field.name}`" @click="editField(field)"
            >编辑</a-button
          >
          <a-popconfirm
            title="删除字段？已有文件使用该字段时无法删除。"
            ok-text="删除"
            cancel-text="取消"
            @confirm="deleteField(field)"
          >
            <a-button
              type="link"
              danger
              :loading="deleting === field.key"
              :aria-label="`删除字段 ${field.name}`"
              >删除</a-button
            >
          </a-popconfirm>
        </div>
      </li>
    </ul>
    <a-button v-if="error && !loading" @click="loadFields">重新读取</a-button>
    <a-modal
      v-model:open="editorOpen"
      :title="editingKey ? '编辑属性字段' : '添加属性字段'"
      ok-text="保存字段"
      cancel-text="取消"
      :confirm-loading="saving"
      :mask-closable="!saving"
      @ok="saveField"
    >
      <a-alert v-if="formError" type="error" show-icon :message="formError" role="alert" />
      <a-form layout="vertical" :model="draft" style="margin-top: 16px">
        <a-form-item label="字段名称" required
          ><a-input
            v-model:value="draft.name"
            aria-label="字段名称"
            :maxlength="64"
            :disabled="saving"
        /></a-form-item>
        <a-form-item
          label="字段类型"
          :html-for="typeControlId"
          required
          :help="editingKey ? '字段创建后类型固定；需要其他类型时请新建字段。' : ''"
          ><a-select
            :id="typeControlId"
            v-model:value="draft.type"
            aria-label="字段类型"
            :disabled="Boolean(editingKey) || saving"
            :options="typeOptions"
        /></a-form-item>
        <a-form-item label="字段说明"
          ><a-textarea
            v-model:value="draft.description"
            aria-label="字段说明"
            :maxlength="500"
            :auto-size="{ minRows: 2, maxRows: 4 }"
            :disabled="saving"
            placeholder="说明这个字段描述什么，帮助正确填写和检索"
        /></a-form-item>
        <a-form-item
          v-if="['select', 'multi_select'].includes(draft.type)"
          label="可选值"
          required
          help="每行一个选项；正在使用的选项需要先从文件属性中清除才能移除。"
          ><a-textarea
            v-model:value="optionText"
            aria-label="可选值，每行一个"
            :auto-size="{ minRows: 3, maxRows: 8 }"
            :disabled="saving"
        /></a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>

<script setup>
import { ref, reactive, watch, useId } from 'vue'
import { message } from 'ant-design-vue'
import { metadataApi } from '@/apis/knowledge_api'
import { metadataTypeNames } from '../model/metadata'

const props = defineProps({ kbId: { type: String, required: true } })
const emit = defineEmits(['changed'])
const typeControlId = useId()
const fields = ref([])
const loading = ref(false)
const error = ref('')
const deleting = ref('')
const editorOpen = ref(false)
const editingKey = ref('')
const initialField = ref(null)
const saving = ref(false)
const formError = ref('')
const optionText = ref('')
const draft = reactive({ name: '', type: 'text', description: '' })
const typeOptions = Object.entries(metadataTypeNames).map(([value, label]) => ({ value, label }))
let loadVersion = 0

/** 只应用当前知识库最近一次读取结果。 */
async function loadFields() {
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  try {
    const result = await metadataApi.getFields(props.kbId)
    if (version === loadVersion) fields.value = result.fields
  } catch (reason) {
    if (version === loadVersion) error.value = reason.message || '读取字段失败'
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

function editField(field) {
  initialField.value = field ? { ...field, options: [...field.options] } : null
  editingKey.value = field?.key || ''
  Object.assign(draft, {
    name: field?.name || '',
    type: field?.type || 'text',
    description: field?.description || ''
  })
  optionText.value = (field?.options || []).join('\n')
  formError.value = ''
  editorOpen.value = true
}

async function saveField() {
  if (saving.value) return
  formError.value = ''
  if (!draft.name.trim()) {
    formError.value = '请填写字段名称'
    return
  }
  const options = ['select', 'multi_select'].includes(draft.type)
    ? optionText.value
        .split('\n')
        .map((value) => value.trim())
        .filter(Boolean)
    : []
  if (['select', 'multi_select'].includes(draft.type) && !options.length) {
    formError.value = '请至少填写一个选项'
    return
  }
  saving.value = true
  try {
    const data = { name: draft.name.trim(), description: draft.description, options }
    if (editingKey.value) {
      const changes = Object.fromEntries(
        Object.entries(data).filter(
          ([key, value]) => JSON.stringify(value) !== JSON.stringify(initialField.value[key])
        )
      )
      await metadataApi.updateField(props.kbId, editingKey.value, changes)
    } else await metadataApi.createField(props.kbId, { ...data, type: draft.type })
    editorOpen.value = false
    message.success('字段已保存')
    await loadFields()
    emit('changed')
  } catch (reason) {
    formError.value = reason.message || '保存字段失败'
  } finally {
    saving.value = false
  }
}

async function deleteField(field) {
  deleting.value = field.key
  error.value = ''
  try {
    await metadataApi.deleteField(props.kbId, field.key)
    await loadFields()
    message.success('字段已删除')
    emit('changed')
  } catch (reason) {
    error.value = reason.message || '删除字段失败'
  } finally {
    deleting.value = ''
  }
}

watch(
  () => props.kbId,
  () => {
    editorOpen.value = false
    loadFields()
  },
  { immediate: true }
)
</script>

<style scoped>
.metadata-heading,
.metadata-field-list li {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.metadata-heading p,
.metadata-field-list p {
  color: var(--color-text-secondary);
  margin: 0 0 12px;
}
.metadata-field-list {
  list-style: none;
  padding: 0;
  margin: 16px 0;
}
.metadata-field-list li {
  padding: 14px 0;
  border-bottom: 1px solid var(--gray-150);
}
.metadata-field-list strong {
  margin-right: 8px;
  overflow-wrap: anywhere;
}
.metadata-field-list p {
  margin-top: 6px;
}
.field-actions {
  display: flex;
  flex-shrink: 0;
}
@media (max-width: 640px) {
  .metadata-heading {
    flex-wrap: wrap;
  }
  .metadata-field-list li {
    gap: 4px;
  }
}
</style>
