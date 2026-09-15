<template>
  <section>
    <h2>我的个人文件与会话附件</h2>
    <a-space class="personal-trash-toolbar">
      <a-button :loading="loading" @click="load">刷新个人回收站</a-button>
      <span v-if="!loading && !error">共 {{ total }} 项</span>
    </a-space>
    <a-alert v-if="error" type="error" show-icon :message="error" />
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      :scroll="{ x: 750 }"
      :pagination="{ current: page, pageSize: 20, total, showSizeChanger: false }"
      :locale="{ emptyText: error ? '加载失败，请重试' : '个人回收站为空' }"
      @change="changePage"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          {{ record.name }}
          <div class="original-path">{{ record.paths.join('；') }}</div>
        </template>
        <template v-else-if="column.key === 'kind'">{{
          record.kind === 'attachment' ? '会话附件' : '个人文件/目录'
        }}</template>
        <template v-else-if="column.key === 'purge_after'">{{
          formatDate(record.purge_after)
        }}</template>
        <template v-else-if="column.key === 'state'">
          {{ record.state === 'trashed' && isExpired(record)
            ? '已到期，等待清理'
            : stateLabels[record.state] || record.state }}
          <div v-if="record.error" class="trash-error">{{ record.error }}</div>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button
            v-if="record.state === 'trashed'"
            :disabled="!!busy || isExpired(record)"
            :loading="busy === record.id"
            @click="act(record, 'restore')"
            >恢复</a-button
          >
          <a-button
            v-else
            :disabled="!!busy"
            :loading="busy === record.id"
            @click="act(record, 'retry')"
            >重试处理</a-button
          >
        </template>
      </template>
    </a-table>
  </section>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { personalTrashApi } from '@/apis/personal_trash_api'

const now = ref(Date.now())
const isExpired = (record) => Date.parse(record.purge_after) <= now.value
let expiryTimer
onMounted(() => { expiryTimer = setInterval(() => { now.value = Date.now() }, 30000) })
onUnmounted(() => clearInterval(expiryTimer))
const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const busy = ref('')
const error = ref('')
let generation = 0
const columns = [
  { title: '原文件', key: 'name' },
  { title: '类型', key: 'kind', width: 130 },
  { title: '到期时间', key: 'purge_after', width: 170 },
  { title: '状态', key: 'state', width: 180 },
  { title: '操作', key: 'action', width: 110 }
]
const stateLabels = {
  pending_delete: '移入处理中',
  trashed: '可恢复',
  restoring: '恢复处理中',
  purging: '到期清理中'
}
const formatDate = (value) => new Date(value).toLocaleString('zh-CN', { hour12: false })
async function load() {
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    const result = await personalTrashApi.list(page.value)
    if (current !== generation) return
    items.value = result.items
    total.value = result.total
    if (!items.value.length && total.value && page.value > 1) {
      page.value -= 1
      return load()
    }
  } catch (err) {
    if (current === generation) error.value = err.message || '个人回收站加载失败'
  } finally {
    if (current === generation) loading.value = false
  }
}
function changePage(pagination) {
  page.value = pagination.current
  load()
}
async function act(record, action) {
  if (busy.value) return
  busy.value = record.id
  error.value = ''
  try {
    await personalTrashApi[action](record.id)
    message.success(action === 'restore' ? '已恢复到原位置' : '处理完成')
    await load()
  } catch (err) {
    error.value = err.message || '操作失败，文件仍保留在回收站'
  } finally {
    busy.value = ''
  }
}
onMounted(load)
</script>

<style scoped>
.personal-trash-toolbar {
  margin: 12px 0;
}
.original-path {
  color: var(--gray-600);
  overflow-wrap: anywhere;
  font-size: 12px;
}
.trash-error {
  color: var(--color-error, #c53030);
}
h2 {
  font-size: 16px;
}
</style>
