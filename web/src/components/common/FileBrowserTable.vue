<template>
  <section class="file-browser-table-shell">
    <div class="file-browser-header">
      <div class="file-browser-path">
        <slot name="toolbar-left">
          <nav class="file-browser-breadcrumbs" aria-label="文件路径">
            <button
              v-for="(item, index) in resolvedBreadcrumbs"
              :key="item.key || item.path || item.name || index"
              type="button"
              class="file-browser-breadcrumb-item"
              :class="{ active: isCurrentBreadcrumb(index) }"
              :disabled="isBreadcrumbDisabled(item, index)"
              :title="item.title || item.path || item.name"
              @click="handleBreadcrumbClick(item, index)"
            >
              {{ item.name || rootLabel }}
            </button>
            <slot name="breadcrumb-suffix" />
          </nav>
        </slot>
      </div>

      <div class="file-browser-actions">
        <slot name="toolbar-actions" />
        <a-tooltip v-if="refreshable" title="刷新">
          <a-button
            type="text"
            class="file-browser-icon-button"
            :loading="refreshing"
            aria-label="刷新"
            @click="$emit('refresh')"
          >
            <template #icon><RotateCw :size="16" /></template>
          </a-button>
        </a-tooltip>
      </div>
    </div>

    <slot name="before-table" />

    <a-table
      :columns="columns"
      :data-source="rows"
      :row-key="rowKey"
      :loading="loading"
      :pagination="tablePagination"
      :row-selection="tableSelection"
      :row-class-name="resolveRowClassName"
      :custom-row="resolveCustomRow"
      class="file-browser-ant-table"
      size="small"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, text, record, index }">
        <slot
          :name="cellSlotName(column)"
          :row="record"
          :record="record"
          :column="column"
          :text="text"
          :index="index"
        >
          <template v-if="isNameColumn(column)">
            <slot name="name" :row="record" :record="record" :text="text" :index="index">
              <span class="file-browser-default-name">
                <FileTypeIcon :name="resolveRowName(record)" :is-dir="resolveRowIsDir(record)" />
                <span class="file-browser-name-text" :title="resolveRowName(record)">
                  {{ resolveRowName(record) }}
                </span>
              </span>
            </slot>
          </template>
          <template v-else-if="isStatusColumn(column)">
            <slot name="status" :row="record" :record="record" :text="text" :index="index">
              {{ text || '-' }}
            </slot>
          </template>
          <template v-else-if="isActionColumn(column)">
            <div class="file-browser-row-actions" @click.stop>
              <slot name="row-actions" :row="record" :record="record" :index="index" />
            </div>
          </template>
          <template v-else>
            {{ text ?? '-' }}
          </template>
        </slot>
      </template>

      <template #emptyText>
        <slot name="empty">
          <a-empty :description="emptyText" />
        </slot>
      </template>
    </a-table>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { RotateCw } from 'lucide-vue-next'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  columns: { type: Array, default: () => [] },
  breadcrumbs: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  rowKey: { type: [String, Function], default: 'key' },
  selectedKey: { type: [String, Number], default: '' },
  rowClickable: { type: Boolean, default: true },
  rowClassName: { type: [String, Function], default: '' },
  pagination: { type: Object, default: null },
  selection: { type: Object, default: null },
  emptyText: { type: String, default: '暂无文件' },
  rootLabel: { type: String, default: '文件' },
  refreshable: { type: Boolean, default: false },
  refreshing: { type: Boolean, default: false }
})

const emit = defineEmits([
  'open-row',
  'breadcrumb-click',
  'update:selectedKeys',
  'page-change',
  'table-change',
  'refresh'
])

const resolvedBreadcrumbs = computed(() => {
  if (props.breadcrumbs.length) return props.breadcrumbs
  return [{ key: 'root', name: props.rootLabel, path: '/' }]
})

const tablePagination = computed(() => {
  if (!props.pagination) return false
  return {
    size: 'small',
    hideOnSinglePage: true,
    ...props.pagination
  }
})

const tableSelection = computed(() => {
  if (!props.selection) return null

  const disabledKeys = new Set(props.selection.disabledKeys || [])
  const getCheckboxProps = props.selection.getCheckboxProps

  return {
    selectedRowKeys: props.selection.selectedRowKeys || props.selection.selectedKeys || [],
    preserveSelectedRowKeys: Boolean(props.selection.preserveSelectedRowKeys),
    hideSelectAll: Boolean(props.selection.hideSelectAll),
    getCheckboxProps: (row) => {
      if (typeof getCheckboxProps === 'function') {
        return getCheckboxProps(row)
      }
      return { disabled: disabledKeys.has(resolveRowKey(row)) }
    },
    onChange: (keys, selectedRows, info) => {
      if (typeof props.selection.onChange === 'function') {
        props.selection.onChange(keys, selectedRows, info)
      }
      emit('update:selectedKeys', keys)
    }
  }
})

const resolveRowKey = (row) => {
  if (typeof props.rowKey === 'function') return props.rowKey(row)
  return row?.[props.rowKey]
}

const resolveRowName = (row) => row?.name || row?.filename || row?.displayName || ''

const resolveRowIsDir = (row) => Boolean(row?.isDir ?? row?.is_dir ?? row?.is_folder)

const columnKey = (column) => column?.key || column?.dataIndex || ''

const cellSlotName = (column) => `cell-${columnKey(column)}`

const isNameColumn = (column) => ['name', 'filename'].includes(columnKey(column))

const isStatusColumn = (column) => columnKey(column) === 'status'

const isActionColumn = (column) => ['action', 'actions'].includes(columnKey(column))

const isCurrentBreadcrumb = (index) => index === resolvedBreadcrumbs.value.length - 1

const isBreadcrumbDisabled = (item, index) => Boolean(item.disabled || isCurrentBreadcrumb(index))

const handleBreadcrumbClick = (item, index) => {
  if (isBreadcrumbDisabled(item, index)) return
  emit('breadcrumb-click', { item, index })
}

const resolveRowClassName = (row, index) => {
  const classes = []
  if (props.rowClickable) classes.push('file-browser-row-clickable')
  if (props.selectedKey && String(resolveRowKey(row)) === String(props.selectedKey)) {
    classes.push('file-browser-row-selected')
  }

  if (typeof props.rowClassName === 'function') {
    const customClass = props.rowClassName(row, index)
    if (customClass) classes.push(customClass)
  } else if (props.rowClassName) {
    classes.push(props.rowClassName)
  }

  return classes.join(' ')
}

const isInteractiveTarget = (event) => {
  return Boolean(
    event.target?.closest?.(
      'button,a,input,textarea,select,[role="button"],.ant-checkbox-wrapper,.ant-dropdown-trigger,.ant-popover'
    )
  )
}

const resolveCustomRow = (row, index) => {
  if (!props.rowClickable) return {}

  return {
    tabindex: 0,
    onClick: (event) => {
      if (isInteractiveTarget(event)) return
      emit('open-row', row, index)
    },
    onKeydown: (event) => {
      if (event.key !== 'Enter') return
      if (isInteractiveTarget(event)) return
      event.preventDefault()
      emit('open-row', row, index)
    }
  }
}

const handleTableChange = (pagination, filters, sorter, extra) => {
  emit('table-change', { pagination, filters, sorter, extra })

  if (!props.pagination || !pagination) return
  const current = pagination.current
  const pageSize = pagination.pageSize
  if (current === props.pagination.current && pageSize === props.pagination.pageSize) return

  emit('page-change', { page: current, pageSize, pagination, filters, sorter, extra })
}
</script>

<style scoped lang="less">
.file-browser-table-shell {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--gray-0);
  overflow: hidden;
}

.file-browser-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  padding: 0 14px;
  border-bottom: 1px solid var(--gray-100);
  flex: 0 0 auto;
}

.file-browser-path {
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 1 1 auto;
}

.file-browser-breadcrumbs {
  display: flex;
  align-items: center;
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
  font-size: 14px;
  line-height: 26px;
}

.file-browser-breadcrumb-item {
  display: inline-flex;
  align-items: center;
  max-width: 220px;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-800);
  cursor: pointer;
  font: inherit;
  font-weight: 500;
  white-space: nowrap;
  transition: color 0.16s ease;

  &::after {
    content: '/';
    margin: 0 8px;
    color: var(--gray-300);
    font-weight: 400;
  }

  &:last-of-type::after {
    display: none;
  }

  &:hover:not(:disabled) {
    color: var(--main-600);
  }

  &.active,
  &:disabled {
    color: var(--gray-900);
    cursor: default;
  }
}

.file-browser-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
  flex: 0 0 auto;
}

.file-browser-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border-radius: 6px;
  color: var(--gray-600);
  background: var(--gray-0);
  box-shadow: 0 0 0 1px var(--shadow-1);
  transition:
    background-color 0.16s ease,
    color 0.16s ease,
    box-shadow 0.16s ease;

  &:hover {
    background: var(--gray-50);
    color: var(--main-color);
    box-shadow: 0 0 0 1px var(--main-100);
  }
}

.file-browser-ant-table {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  padding: 0 8px;
  background: var(--gray-0);
}

.file-browser-ant-table :deep(.ant-spin-nested-loading),
.file-browser-ant-table :deep(.ant-spin-container) {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}

.file-browser-ant-table :deep(.ant-table) {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  background: var(--gray-0);
}

.file-browser-ant-table :deep(.ant-table-container),
.file-browser-ant-table :deep(.ant-table-content),
.file-browser-ant-table :deep(table) {
  min-width: 100%;
}

.file-browser-ant-table :deep(.ant-table-thead > tr > th) {
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 8px 10px;
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-0);
}

.file-browser-ant-table :deep(.ant-table-tbody > tr > td) {
  padding: 7px 10px;
  background: var(--gray-0);
  color: var(--gray-700);
  font-size: 13px;
  line-height: 20px;
  border-bottom: 1px solid var(--gray-50);
  transition:
    background-color 0.16s ease,
    color 0.16s ease;
}

.file-browser-ant-table :deep(.ant-table-tbody > tr.file-browser-row-clickable) {
  cursor: pointer;
}

.file-browser-ant-table :deep(.ant-table-tbody > tr:hover > td) {
  background: var(--main-20);
  color: var(--gray-1000);
}

.file-browser-ant-table :deep(.ant-table-tbody > tr.file-browser-row-selected > td) {
  background: var(--main-20);
  color: var(--gray-1000);
}

.file-browser-ant-table :deep(.ant-table-tbody > tr.file-browser-row-selected > td:first-child) {
  box-shadow: inset 3px 0 0 var(--main-color);
}

.file-browser-ant-table :deep(.ant-table-tbody > tr.ant-table-row-selected > td) {
  background: var(--main-5);
}

.file-browser-ant-table :deep(.ant-table-tbody > tr.ant-table-row-selected:hover > td) {
  background: var(--main-20);
}

.file-browser-ant-table :deep(.ant-table-tbody > tr.is-deleting > td) {
  opacity: 0.62;
}

.file-browser-ant-table :deep(.ant-table-cell) {
  vertical-align: middle;
}

.file-browser-ant-table :deep(.ant-table-pagination) {
  margin: 10px 0;
  padding: 0 6px;
}

.file-browser-default-name {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  min-width: 0;
  gap: 8px;
  vertical-align: middle;
}

.file-browser-name-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-browser-row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
}

@media (max-width: 767px) {
  .file-browser-header {
    align-items: stretch;
    flex-direction: column;
    gap: 8px;
    padding: 10px 14px;
  }

  .file-browser-actions {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
