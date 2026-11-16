<template>
  <div class="workspace-path-picker" :class="{ disabled }">
    <div class="picker-toolbar">
      <nav class="picker-breadcrumbs" aria-label="Workspace 目录路径">
        <button
          v-for="item in breadcrumbs"
          :key="item.path"
          type="button"
          :disabled="disabled || item.path === currentPath"
          @click="openDirectory(item.path)"
        >
          {{ item.name }}
        </button>
      </nav>
      <div class="picker-actions">
        <button
          type="button"
          class="picker-text-action"
          :disabled="disabled || loading"
          @click="startCreatingFolder"
        >
          <FolderPlus :size="15" />
          新建文件夹
        </button>
        <button
          type="button"
          class="picker-icon-action"
          aria-label="刷新目录"
          :disabled="disabled || loading"
          @click="loadEntries(currentPath)"
        >
          <RotateCw :size="15" />
        </button>
      </div>
    </div>

    <form v-if="creatingFolder" class="picker-create-row" @submit.prevent="createFolder">
      <FolderPlus :size="16" />
      <input
        ref="folderNameInput"
        v-model="folderName"
        maxlength="255"
        placeholder="文件夹名称"
        aria-label="文件夹名称"
        :disabled="creating"
      />
      <button type="submit" :disabled="!folderName.trim() || creating">
        {{ creating ? '创建中' : '创建' }}
      </button>
      <button
        type="button"
        class="picker-icon-action"
        aria-label="取消新建文件夹"
        :disabled="creating"
        @click="cancelCreatingFolder"
      >
        <X :size="15" />
      </button>
    </form>

    <div v-if="selectionMode === 'directory'" class="picker-selection" aria-live="polite">
      <Check v-if="selectedDirectory" :size="14" />
      <span :title="selectedDirectory">{{ selectedDirectory || '请选择项目目录' }}</span>
    </div>

    <div class="picker-list" :class="{ loading }">
      <a-spin v-if="loading" />
      <template v-else-if="hasVisibleEntries">
        <button
          v-for="entry in directoryEntries"
          :key="entry.path"
          type="button"
          class="picker-row picker-directory"
          :class="{ selected: isSelectedDirectory(entry.path) }"
          :disabled="disabled"
          @click="openDirectory(entry.path)"
        >
          <Folder :size="16" />
          <span :title="entry.path">{{ entry.name }}</span>
          <Check v-if="isSelectedDirectory(entry.path)" :size="15" class="picker-check" />
          <ChevronRight v-else :size="15" />
        </button>

        <label
          v-for="entry in fileEntries"
          :key="entry.path"
          class="picker-row picker-file"
          :class="{ disabled: !isFileSelectable(entry) }"
        >
          <a-checkbox
            :checked="selectedPathSet.has(entry.path)"
            :disabled="disabled || !isFileSelectable(entry)"
            @change="toggleFile(entry.path, $event.target.checked)"
          />
          <FileTypeIcon :name="entry.path" :size="16" />
          <span :title="entry.path">{{ entry.name }}</span>
          <small>{{ formatFileSize(entry.size) }}</small>
        </label>
      </template>
      <div
        v-else
        class="picker-empty"
        :class="{ error: Boolean(error) }"
        :role="error ? 'alert' : 'status'"
      >
        {{ error || '当前目录为空' }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Check, ChevronRight, Folder, FolderPlus, RotateCw, X } from '@lucide/vue'
import { createWorkspaceDirectory, getWorkspaceTree } from '@/apis/workspace_api'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'

const props = defineProps({
  modelValue: { type: [String, Array], default: '' },
  selectionMode: {
    type: String,
    default: 'directory',
    validator: (value) => ['directory', 'files'].includes(value)
  },
  active: { type: Boolean, default: true },
  disabled: { type: Boolean, default: false },
  includeUnboundProjectDirs: { type: Boolean, default: false },
  unselectableDirectories: { type: Array, default: () => ['/'] },
  isFileSelectable: { type: Function, default: () => true }
})
const emit = defineEmits(['update:modelValue', 'loading-change'])

const entries = ref([])
const currentPath = ref('/')
const loading = ref(false)
const error = ref('')
const creatingFolder = ref(false)
const creating = ref(false)
const folderName = ref('')
const folderNameInput = ref(null)
let requestVersion = 0

const normalizePath = (path) => {
  const normalized = String(path || '/').replace(/^\/+|\/+$/g, '')
  return normalized ? `/${normalized}` : '/'
}

const breadcrumbs = computed(() => {
  const items = [{ name: 'Workspace', path: '/' }]
  let path = ''
  for (const name of currentPath.value.split('/').filter(Boolean)) {
    path += `/${name}`
    items.push({ name, path })
  }
  return items
})
const selectedDirectory = computed(() =>
  props.selectionMode === 'directory' && props.modelValue ? normalizePath(props.modelValue) : ''
)
const selectedPathSet = computed(
  () => new Set(Array.isArray(props.modelValue) ? props.modelValue : [])
)
const directoryEntries = computed(() => entries.value.filter((entry) => entry.is_dir))
const fileEntries = computed(() =>
  props.selectionMode === 'files' ? entries.value.filter((entry) => !entry.is_dir) : []
)
const hasVisibleEntries = computed(
  () => directoryEntries.value.length > 0 || fileEntries.value.length > 0
)
const blockedDirectorySet = computed(
  () => new Set(props.unselectableDirectories.map((path) => normalizePath(path)))
)

const getErrorMessage = (cause, fallback) =>
  cause?.response?.data?.detail || cause?.message || fallback

const formatFileSize = (size) => {
  if (!Number.isFinite(size)) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

const isSelectedDirectory = (path) =>
  props.selectionMode === 'directory' && selectedDirectory.value === normalizePath(path)

const loadEntries = async (path = currentPath.value) => {
  const version = ++requestVersion
  const targetPath = normalizePath(path)
  loading.value = true
  emit('loading-change', true)
  error.value = ''
  try {
    const response = await getWorkspaceTree(
      targetPath,
      false,
      false,
      props.includeUnboundProjectDirs
    )
    if (version !== requestVersion) return
    currentPath.value = targetPath
    entries.value = Array.isArray(response?.entries) ? response.entries : []
  } catch (cause) {
    if (version !== requestVersion) return
    entries.value = []
    error.value = getErrorMessage(cause, '目录加载失败')
  } finally {
    if (version === requestVersion) {
      loading.value = false
      emit('loading-change', false)
    }
  }
}

const openDirectory = (path) => {
  const targetPath = normalizePath(path)
  if (props.selectionMode === 'directory') {
    emit('update:modelValue', blockedDirectorySet.value.has(targetPath) ? '' : targetPath)
  }
  cancelCreatingFolder()
  void loadEntries(targetPath)
}

const toggleFile = (path, checked) => {
  const next = new Set(selectedPathSet.value)
  if (checked) next.add(path)
  else next.delete(path)
  emit('update:modelValue', [...next])
}

const startCreatingFolder = async () => {
  folderName.value = ''
  creatingFolder.value = true
  await nextTick()
  folderNameInput.value?.focus()
}

const cancelCreatingFolder = () => {
  if (creating.value) return
  creatingFolder.value = false
  folderName.value = ''
}

const createFolder = async () => {
  const name = folderName.value.trim()
  if (!name || creating.value) return
  creating.value = true
  try {
    const response = await createWorkspaceDirectory(currentPath.value, name)
    const createdPath = response?.entry?.path
    creatingFolder.value = false
    folderName.value = ''
    if (props.selectionMode === 'directory' && createdPath) {
      openDirectory(createdPath)
    } else {
      await loadEntries(currentPath.value)
    }
    message.success('文件夹已创建')
  } catch (cause) {
    message.error(getErrorMessage(cause, '文件夹创建失败'))
  } finally {
    creating.value = false
  }
}

watch(
  () => props.active,
  (active) => {
    if (!active) {
      requestVersion += 1
      loading.value = false
      emit('loading-change', false)
      cancelCreatingFolder()
      return
    }
    const initialPath =
      props.selectionMode === 'directory' && props.modelValue
        ? normalizePath(props.modelValue)
        : '/'
    currentPath.value = initialPath
    void loadEntries(initialPath)
  },
  { immediate: true }
)
</script>

<style scoped lang="less">
.workspace-path-picker {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
}

.picker-toolbar,
.picker-actions,
.picker-create-row,
.picker-selection,
.picker-row {
  display: flex;
  align-items: center;
}

.picker-toolbar {
  min-height: 30px;
  justify-content: space-between;
  gap: 12px;
}

.picker-breadcrumbs {
  display: flex;
  min-width: 0;
  align-items: center;
  overflow-x: auto;
  white-space: nowrap;
}

.picker-breadcrumbs button {
  padding: 2px 0;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 12px;
}

.picker-breadcrumbs button:not(:last-child)::after {
  margin: 0 5px;
  color: var(--color-text-tertiary);
  content: '/';
}

.picker-breadcrumbs button:disabled {
  color: var(--color-text);
  cursor: default;
}

.picker-actions {
  flex: 0 0 auto;
  gap: 4px;
}

.picker-actions button,
.picker-create-row button {
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.picker-text-action {
  display: inline-flex;
  height: 28px;
  align-items: center;
  gap: 5px;
  padding: 0 7px;
  font-size: 12px;
}

.picker-icon-action {
  display: inline-flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.picker-actions button:hover:not(:disabled),
.picker-create-row button:hover:not(:disabled) {
  background: var(--gray-50);
  color: var(--color-text);
}

.picker-actions button:disabled,
.picker-create-row button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.picker-create-row {
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  color: var(--color-text-secondary);
}

.picker-create-row input {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  font-size: 13px;
}

.picker-create-row button[type='submit'] {
  height: 26px;
  padding: 0 7px;
  color: var(--main-700);
  font-size: 12px;
}

.picker-selection {
  min-width: 0;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.picker-selection span {
  overflow: hidden;
  color: var(--color-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-list {
  display: flex;
  min-height: 180px;
  max-height: 300px;
  flex-direction: column;
  overflow-y: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.picker-list.loading,
.picker-list > .ant-spin {
  align-items: center;
  justify-content: center;
}

.picker-row {
  width: 100%;
  min-width: 0;
  min-height: 36px;
  gap: 8px;
  padding: 7px 10px;
  border: 0;
  border-bottom: 1px solid var(--gray-100);
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  text-align: left;
}

.picker-row:last-child {
  border-bottom: 0;
}

.picker-row:hover:not(.disabled),
.picker-row.selected {
  background: var(--gray-25);
}

.picker-row:focus-visible,
.picker-actions button:focus-visible,
.picker-create-row button:focus-visible,
.picker-breadcrumbs button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}

.picker-row > span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-row > small {
  flex: 0 0 auto;
  color: var(--color-text-secondary);
  font-size: 11px;
}

.picker-row.disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.picker-directory > svg:first-child,
.picker-file > svg {
  flex: 0 0 auto;
  color: var(--color-text-secondary);
}

.picker-check {
  color: var(--main-color);
}

.picker-empty {
  margin: auto;
  padding: 20px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.picker-empty.error {
  color: var(--color-error-600);
}

@media (max-width: 520px) {
  .picker-toolbar {
    align-items: flex-start;
  }

  .picker-text-action {
    width: 28px;
    overflow: hidden;
    padding: 0 6px;
    white-space: nowrap;
  }
}
</style>
