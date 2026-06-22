<template>
  <FileBrowserTable
    class="workspace-file-list"
    :rows="entries"
    :columns="columns"
    row-key="path"
    :selected-key="selectedPath"
    :row-class-name="rowClassName"
    :loading="loading"
    :breadcrumbs="resolvedBreadcrumbItems"
    :root-label="rootLabel"
    :pagination="pagination"
    :selection="tableSelection"
    empty-text="当前文件夹为空"
    @open-row="(entry) => $emit('select-entry', entry)"
    @breadcrumb-click="handleBreadcrumbClick"
    @page-change="(payload) => $emit('page-change', payload)"
  >
    <template #toolbar-actions>
      <span class="entry-count">{{ entryCountText }}</span>
      <a-tooltip v-if="!readonly" title="多选">
        <a-button
          size="small"
          class="lucide-icon-btn"
          :type="effectiveSelectionMode ? 'primary' : 'default'"
          aria-label="多选"
          @click="toggleSelectionMode"
        >
          <ListChecks :size="14" />
        </a-button>
      </a-tooltip>
      <a-button
        v-if="effectiveSelectionMode"
        size="small"
        danger
        :disabled="!selectedPaths.length"
        :loading="deletingPaths.length > 0"
        @click="$emit('delete-selected')"
      >
        删除选中
      </a-button>
    </template>

    <template #name="{ row }">
      <span class="name-cell">
        <FileTypeIcon :name="row.name || row.path" :is-dir="row.is_dir" :size="17" />
        <span class="entry-name" :title="row.name">{{ row.name }}</span>
      </span>
    </template>

    <template #cell-size="{ row }">
      <span>{{ row.is_dir ? '-' : formatFileSize(row.size) }}</span>
    </template>

    <template #cell-modified_at="{ row }">
      <span>{{ formatRelativeTime(row.modified_at) }}</span>
    </template>

    <template #row-actions="{ row }">
      <a-dropdown v-if="!row.is_dir || !readonly" :trigger="['click']">
        <button
          type="button"
          class="more-action"
          :disabled="isDeleting(row.path)"
          aria-label="更多操作"
          @click.stop
        >
          <MoreHorizontal :size="16" />
        </button>
        <template #overlay>
          <a-menu>
            <a-menu-item v-if="!row.is_dir" key="download" @click="$emit('download-entry', row)">
              <span class="menu-item-content">
                <Download :size="14" />
                <span>下载</span>
              </span>
            </a-menu-item>
            <a-menu-item v-if="!readonly" key="delete" danger @click="$emit('delete-entry', row)">
              <span class="menu-item-content">
                <Trash2 :size="14" />
                <span>删除</span>
              </span>
            </a-menu-item>
          </a-menu>
        </template>
      </a-dropdown>
    </template>
  </FileBrowserTable>
</template>

<script setup>
import { computed } from 'vue'
import { Download, ListChecks, MoreHorizontal, Trash2 } from 'lucide-vue-next'
import FileBrowserTable from '@/components/common/FileBrowserTable.vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import { formatFileSize, formatRelativeTime } from '@/utils/file_utils'

const props = defineProps({
  entries: { type: Array, default: () => [] },
  currentPath: { type: String, default: '/' },
  selectedPath: { type: String, default: '' },
  selectedPaths: { type: Array, default: () => [] },
  deletingPaths: { type: Array, default: () => [] },
  selectionMode: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
  breadcrumbItems: { type: Array, default: null },
  rootLabel: { type: String, default: '工作区' },
  pagination: { type: Object, default: null }
})

const emit = defineEmits([
  'select-entry',
  'select-path',
  'breadcrumb-click',
  'update:selectedPaths',
  'update:selectionMode',
  'delete-selected',
  'delete-entry',
  'download-entry',
  'page-change'
])

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
  { title: '大小', dataIndex: 'size', key: 'size', width: 86 },
  { title: '修改时间', dataIndex: 'modified_at', key: 'modified_at', width: 126 },
  { title: '操作', key: 'action', dataIndex: 'path', width: 58, align: 'center' }
]

const deletingPathSet = computed(() => new Set(props.deletingPaths))
const entryPathSet = computed(() => new Set(props.entries.map((entry) => entry.path)))
const normalizedCurrentPath = computed(() => (props.currentPath || '/').replace(/\/+$/, '') || '/')
const effectiveSelectionMode = computed(() => !props.readonly && props.selectionMode)
const entryCountText = computed(() => {
  if (props.pagination?.total !== undefined) {
    return `${props.pagination.total} 项`
  }
  return `${props.entries.length} 项`
})

const resolvedBreadcrumbItems = computed(() => {
  if (props.breadcrumbItems?.length) return props.breadcrumbItems

  const normalizedPath = normalizedCurrentPath.value
  if (normalizedPath === '/') {
    return [{ name: props.rootLabel, path: '/' }]
  }

  const segments = normalizedPath.split('/').filter(Boolean)
  return segments.reduce(
    (items, segment) => {
      const parentPath = items[items.length - 1].path
      const path = parentPath === '/' ? `/${segment}` : `${parentPath}/${segment}`
      items.push({ name: segment, path })
      return items
    },
    [{ name: props.rootLabel, path: '/' }]
  )
})

const tableSelection = computed(() => {
  if (!effectiveSelectionMode.value) return null

  return {
    selectedRowKeys: props.selectedPaths,
    getCheckboxProps: (row) => ({ disabled: isDeleting(row.path) }),
    onChange: (keys) => {
      emit(
        'update:selectedPaths',
        keys.filter((path) => entryPathSet.value.has(path))
      )
    }
  }
})

const isDeleting = (path) => deletingPathSet.value.has(path)

const rowClassName = (entry) => (isDeleting(entry.path) ? 'is-deleting' : '')

const toggleSelectionMode = () => {
  if (props.readonly) return
  const nextMode = !props.selectionMode
  emit('update:selectionMode', nextMode)
  if (!nextMode) {
    emit('update:selectedPaths', [])
  }
}

const handleBreadcrumbClick = ({ item, index }) => {
  emit('breadcrumb-click', { item, index })
  emit('select-path', item.path)
}
</script>

<style scoped lang="less">
.workspace-file-list {
  min-width: 0;
  min-height: 0;
}

.entry-count {
  flex: 0 0 auto;
  color: var(--gray-500);
  font-size: 12px;
}

.name-cell {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  min-width: 0;
  gap: 8px;
  vertical-align: middle;
}

.entry-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.more-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;

  &:hover:not(:disabled) {
    background: var(--gray-100);
    color: var(--gray-900);
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
  }
}

.menu-item-content {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
</style>
