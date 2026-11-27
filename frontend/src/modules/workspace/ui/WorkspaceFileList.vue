<template>
  <div
    class="workspace-list-shell"
    @dragenter="handleDragEnter"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @dragend="resetDragState"
    @drop="handleDrop"
  >
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
      :scroll="{ x: 520 }"
      empty-text="当前文件夹为空"
      @open-row="(entry) => $emit('select-entry', entry)"
      @breadcrumb-click="handleBreadcrumbClick"
      @page-change="(payload) => $emit('page-change', payload)"
    >
      <template #toolbar-actions>
        <span class="entry-count">{{ entryCountText }}</span>
        <a-tooltip v-if="!readonly" title="多选">
          <a-button
            class="workspace-toolbar-icon lucide-icon-btn"
            :type="effectiveSelectionMode ? 'primary' : 'default'"
            aria-label="多选"
            :aria-pressed="effectiveSelectionMode"
            @click="toggleSelectionMode"
          >
            <ListChecks :size="16" />
          </a-button>
        </a-tooltip>
        <a-button
          v-if="effectiveSelectionMode"
          danger
          :disabled="!selectedPaths.length"
          :loading="deletingPaths.length > 0"
          @click="$emit('delete-selected')"
        >
          删除选中
        </a-button>
        <slot name="actions" />
      </template>

      <template #name="{ row }">
        <span class="name-cell">
          <FileTypeIcon :name="row.name || row.path" :is-dir="row.is_dir" :size="17" />
          <span class="entry-name" :title="row.title || row.name">{{ row.title || row.name }}</span>
        </span>
      </template>

      <template #cell-size="{ row }">
        <span>{{ row.is_dir ? '-' : formatFileSize(row.size) }}</span>
      </template>

      <template #cell-modified_at="{ row }">
        <span>{{ formatRelativeTime(row.modified_at) }}</span>
      </template>

      <template #row-actions="{ row }">
        <a-dropdown v-if="!row.is_dir || (!readonly && !row.readonly)" :trigger="['click']">
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
              <a-menu-item
                v-if="!readonly && !row.readonly"
                key="delete"
                danger
                @click="$emit('delete-entry', row)"
              >
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

    <Transition name="drop-overlay">
      <div v-if="dragActive" class="drop-overlay" aria-hidden="true">
        <Upload :size="20" />
        <span>松开鼠标，上传到当前文件夹</span>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Download, ListChecks, MoreHorizontal, Trash2, Upload } from '@lucide/vue'
import FileBrowserTable from '@/shared/ui/FileBrowserTable.vue'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import { formatFileSize, formatRelativeTime } from '@/shared/lib/file_utils'
import { collectDragUploadRoots } from '@/modules/workspace'

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
  rootLabel: { type: String, default: '全部文件' },
  pagination: { type: Object, default: null },
  uploadable: { type: Boolean, default: false }
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
  'page-change',
  'upload-drop'
])

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
  { title: '大小', dataIndex: 'size', key: 'size', width: 86 },
  { title: '修改时间', dataIndex: 'modified_at', key: 'modified_at', width: 126 },
  { title: '操作', key: 'action', dataIndex: 'path', width: 58, align: 'center' }
]

const deletingPathSet = computed(() => new Set(props.deletingPaths))
const entryPathSet = computed(() => new Set(props.entries.map((entry) => entry.path)))
const readonlyPathSet = computed(
  () => new Set(props.entries.filter((entry) => entry.readonly).map((entry) => entry.path))
)
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
    getCheckboxProps: (row) => ({ disabled: row.readonly || isDeleting(row.path) }),
    onChange: (keys) => {
      emit(
        'update:selectedPaths',
        keys.filter((path) => entryPathSet.value.has(path) && !readonlyPathSet.value.has(path))
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

const dragOver = ref(false)
const dragActive = computed(() => props.uploadable && dragOver.value)

const hasDragFiles = (event) => Array.from(event.dataTransfer?.types || []).includes('Files')

const handleDragEnter = (event) => {
  if (!props.uploadable || !hasDragFiles(event)) return
  event.preventDefault()
  dragOver.value = true
}

const handleDragOver = (event) => {
  if (!dragActive.value) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
}

const handleDragLeave = (event) => {
  // 子元素间移动会成对触发 enter/leave：relatedTarget 仍在壳内时忽略；
  // 真正离开壳（或行重建丢失 leave 后再次离开）一律复位，避免覆盖层滞留。
  const related = event.relatedTarget
  if (related && event.currentTarget.contains(related)) return
  dragOver.value = false
}

const resetDragState = () => {
  dragOver.value = false
}

const handleDrop = (event) => {
  resetDragState()
  if (!props.uploadable) return
  const roots = collectDragUploadRoots(event.dataTransfer)
  if (!roots.files.length && !roots.directories.length) return
  event.preventDefault()
  emit('upload-drop', roots)
}
</script>

<style scoped lang="less">
.workspace-list-shell {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.workspace-file-list {
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
}

.drop-overlay {
  position: absolute;
  inset: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 2px dashed var(--main-color);
  border-radius: 10px;
  background: color-mix(in srgb, var(--main-color) 8%, var(--gray-0) 92%);
  color: var(--main-color);
  font-size: 14px;
  font-weight: 500;
  // 覆盖层不接收指针事件，drop 始终落在列表外壳上，避免拖拽闪烁。
  pointer-events: none;
}

.drop-overlay-enter-active,
.drop-overlay-leave-active {
  transition: opacity 0.15s ease;
}

.drop-overlay-enter-from,
.drop-overlay-leave-to {
  opacity: 0;
}

.workspace-file-list :deep(.file-browser-ant-table) {
  padding-inline: var(--workspace-content-inset);
}

.workspace-file-list :deep(.ant-table-thead > tr > th) {
  padding: 9px 12px;
  border-bottom-color: var(--gray-150);
  color: var(--gray-600);
  font-weight: 500;
}

.workspace-file-list :deep(.ant-table-thead > tr > th::before) {
  display: none;
}

.workspace-file-list :deep(.ant-table-tbody > tr > td) {
  padding: 9px 12px;
  border-bottom-color: var(--gray-100);
  transition: background-color 150ms ease, color 150ms ease;
}

.workspace-file-list :deep(.ant-table-tbody > tr:not(.ant-table-placeholder):hover > td) {
  background: var(--gray-50);
  color: var(--gray-1000);
}

.workspace-file-list :deep(.ant-table-tbody > tr.file-browser-row-selected > td),
.workspace-file-list :deep(.ant-table-tbody > tr.ant-table-row-selected > td) {
  background: var(--gray-100);
  color: var(--gray-1000);
}

.workspace-file-list :deep(.file-browser-row-clickable:focus-visible) {
  outline: 2px solid var(--gray-600);
  outline-offset: -2px;
}

.workspace-file-list :deep(.file-browser-breadcrumb-item) {
  color: var(--gray-600);

  &:hover:not(:disabled),
  &.active {
    color: var(--gray-1000);
  }
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
  color: var(--gray-400);
  cursor: pointer;
  transition: background-color 150ms ease, color 150ms ease;

  &:hover:not(:disabled) {
    background: var(--gray-150);
    color: var(--gray-1000);
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
  }
}

.workspace-file-list :deep(tr:hover) .more-action,
.workspace-file-list :deep(tr:focus-within) .more-action {
  color: var(--gray-700);
}

.more-action:focus-visible {
  outline: 2px solid var(--gray-700);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  .more-action,
  .drop-overlay-enter-active,
  .drop-overlay-leave-active,
  .workspace-file-list :deep(.ant-table-tbody > tr > td) {
    transition: none;
  }
}

.menu-item-content {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
</style>
