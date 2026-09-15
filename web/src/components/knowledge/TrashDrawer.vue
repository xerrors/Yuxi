<template>
  <component
    :is="embedded ? 'section' : resolveComponent('a-drawer')"
    :open="open"
    title="回收站"
    :width="760"
    @close="emit('update:open', false)"
  >
    <a-alert
      message="文件移入后保留30天，到期自动清理。恢复后将重新出现在文件列表。"
      type="info"
      show-icon
    />
    <a-space class="trash-toolbar">
      <a-button :loading="loading" @click="load">刷新</a-button>
      <span v-if="!loading && !error">共 {{ total }} 项</span>
    </a-space>
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="file_id"
      :scroll="{ x: 660 }"
      :pagination="{ current: page, pageSize: 10, total, showSizeChanger: false }"
      @change="changePage"
      :locale="{ emptyText: error ? '加载未完成，请重试' : loading ? '加载中' : '回收站为空' }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'filename'"
          >{{ record.is_folder ? '文件夹：' : '' }}{{ record.filename }}</template
        >
        <template v-else-if="column.key === 'deleted_at'">{{
          formatDate(record.deleted_at)
        }}</template>
        <template v-else-if="column.key === 'purge_after'">{{
          formatDate(record.purge_after)
        }}</template>
        <template v-else-if="column.key === 'status'">
          <span>{{ record.status === 'trashed' && !canRestore(record) ? '已到期，等待清理' : statusLabel(record.status) }}</span>
          <div v-if="record.error_message" class="trash-error">{{ record.error_message }}</div>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button
            size="small"
            :loading="restoring === record.file_id"
            :disabled="!!restoring || !canRestore(record)"
            @click="restore(record)"
            >恢复</a-button
          >
        </template>
      </template>
    </a-table>
  </component>
</template>

<script setup>
import { ref, watch, resolveComponent, onMounted, onUnmounted } from 'vue'
import { documentApi } from '@/apis/knowledge_api'
import { message } from 'ant-design-vue'

const props = defineProps({
  embedded: Boolean,
  open: Boolean,
  kbId: { type: String, required: true }
})
const emit = defineEmits(['update:open', 'restored'])
const now = ref(Date.now())
let expiryTimer
onMounted(() => { expiryTimer = setInterval(() => { now.value = Date.now() }, 30000) })
onUnmounted(() => clearInterval(expiryTimer))
const items = ref([])
const total = ref(0)
const page = ref(1)
function changePage(pagination) {
  page.value = pagination.current
  load()
}
const loading = ref(false)
const restoring = ref('')
const error = ref('')
let requestVersion = 0
const columns = [
  { title: '文件名', key: 'filename' },
  { title: '删除日期', key: 'deleted_at', width: 155 },
  { title: '到期时间', key: 'purge_after', width: 155 },
  { title: '状态', key: 'status' },
  { title: '操作', key: 'action', width: 80 }
]
const formatDate = (value) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
const statusLabel = (value) =>
  ({ trashed: '待恢复', purging: '清理中', purge_failed: '清理失败', restoring: '恢复中' })[
    value
  ] ||
  value ||
  '未知'
const canRestore = (record) =>
  record.status === 'trashed' && new Date(record.purge_after).getTime() > now.value

async function load() {
  const version = ++requestVersion
  items.value = []
  total.value = 0
  error.value = ''
  if (!props.open || !props.kbId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const result = await documentApi.getTrash(props.kbId, page.value)
    if (version !== requestVersion) return
    items.value = result.items
    total.value = result.total
  } catch (err) {
    if (version === requestVersion) error.value = err.message || '回收站加载失败，请重试'
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

async function restore(record) {
  if (restoring.value || !canRestore(record)) return
  const kbId = props.kbId
  restoring.value = record.file_id
  error.value = ''
  try {
    const result = await documentApi.restoreDocument(kbId, record.file_id)
    if (kbId !== props.kbId || !props.open) return
    message.success(result.message || `已恢复 ${result.restored_count} 项`)
    emit('restored')
    page.value = 1
    await load()
  } catch (err) {
    if (kbId === props.kbId && props.open) error.value = err.message || '恢复失败，请刷新后重试'
  } finally {
    restoring.value = ''
  }
}

watch(
  () => [props.open, props.kbId],
  () => {
    page.value = 1
    load()
  },
  { immediate: true }
)
</script>

<style scoped>
.trash-toolbar {
  margin: 16px 0;
}
.trash-error {
  color: var(--color-error, #c53b3b);
  font-size: 12px;
}
</style>
