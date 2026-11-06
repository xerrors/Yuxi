<template>
  <a-modal
    :open="open"
    title="选择项目目录"
    :width="800"
    centered
    :footer="null"
    :closable="!disabled"
    :mask-closable="!disabled"
    :keyboard="!disabled"
    wrap-class-name="workspace-directory-dialog"
    @cancel="closeDialog"
  >
    <div class="directory-dialog">
      <div class="directory-location">
        <a-input
          v-if="editingPath"
          ref="pathInput"
          v-model:value="pathDraft"
          aria-label="编辑目录路径"
          placeholder="输入个人空间路径"
          @input="schedulePathSearch"
          @press-enter="commitPath"
          @keydown.esc.stop="cancelPathEditing"
        />
        <template v-else>
          <nav class="directory-breadcrumbs" aria-label="目录路径">
            <template v-for="(item, index) in breadcrumbs" :key="item.path">
              <ChevronRight v-if="index" :size="14" aria-hidden="true" />
              <button type="button" :disabled="disabled || loading" @click="browse(item.path)">
                {{ item.name }}
              </button>
            </template>
          </nav>
          <button
            type="button"
            class="directory-edit"
            aria-label="编辑目录路径"
            :disabled="disabled || loading"
            @click="startPathEditing"
          >
            <SquarePen :size="16" aria-hidden="true" />
          </button>
        </template>
      </div>

      <div ref="directoryContent" class="directory-content" :aria-busy="loading">
        <div v-if="loading" class="directory-state"><a-spin /></div>
        <div v-else-if="error" class="directory-state directory-error" role="alert">
          <span>{{ error }}</span>
          <button type="button" @click="browse('/')">返回主目录</button>
        </div>
        <div v-else class="directory-columns" :class="{ 'two-columns': columns.length === 2 }">
          <div v-for="column in columns" :key="column.path" class="directory-column">
            <button
              v-for="entry in visibleEntries(column.entries)"
              :key="entry.path"
              type="button"
              class="directory-row"
              :class="{ selected: highlightedPath === normalizePath(entry.path) }"
              :aria-pressed="highlightedPath === normalizePath(entry.path)"
              :disabled="disabled"
              @click="browse(entry.path)"
            >
              <FolderOpen
                v-if="highlightedPath === normalizePath(entry.path)"
                :size="18"
                aria-hidden="true"
              />
              <Folder v-else :size="18" aria-hidden="true" />
              <span :title="entry.name">{{ entry.name }}</span>
              <ChevronRight :size="15" aria-hidden="true" />
            </button>
            <div v-if="!visibleEntries(column.entries).length" class="directory-empty">
              此文件夹为空
            </div>
          </div>
        </div>
      </div>

      <div class="directory-footer">
        <p class="directory-hint">所选目录即项目工作区，文件直接保存在该目录中，不会另建同名文件夹。</p>
        <div class="directory-footer-tools">
          <a-button :disabled="disabled || loading || editingPath" @click="startCreatingFolder">
            <template #icon><Plus :size="16" aria-hidden="true" /></template>
            新建文件夹
          </a-button>
          <button type="button" class="hidden-toggle" :disabled="disabled" :aria-pressed="showHidden" @click="toggleHidden">
            {{ showHidden ? '隐藏隐藏文件' : '显示隐藏文件' }}
          </button>
        </div>
        <div class="directory-footer-actions">
          <a-button :disabled="disabled" @click="closeDialog">取消</a-button>
          <a-button
            type="primary"
            class="directory-open"
            :disabled="!canOpen"
            :loading="disabled"
            @click="$emit('select', selectedPath)"
          >
            创建并选择
          </a-button>
        </div>
      </div>
    </div>
  </a-modal>

  <a-modal
    v-model:open="folderDialogOpen"
    title="新建文件夹"
    :width="440"
    :z-index="1100"
    centered
    ok-text="创建"
    cancel-text="取消"
    :confirm-loading="creatingFolder"
    :closable="!creatingFolder"
    :mask-closable="!creatingFolder"
    :keyboard="!creatingFolder"
    :cancel-button-props="{ disabled: creatingFolder }"
    :ok-button-props="{ disabled: !folderName.trim() }"
    @ok="createFolder"
    @cancel="cancelCreatingFolder"
  >
    <p class="folder-dialog-description">在“{{ currentDirectoryName }}”中新建文件夹</p>
    <a-input
      ref="folderInput"
      v-model:value="folderName"
      :maxlength="255"
      :disabled="creatingFolder"
      aria-label="文件夹名称"
      placeholder="文件夹名称"
      @input="folderError = ''"
      @press-enter="createFolder"
    />
    <div v-if="folderError" class="folder-dialog-error" role="alert">{{ folderError }}</div>
  </a-modal>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { ChevronRight, Folder, FolderOpen, Plus, SquarePen } from '@lucide/vue'
import { resolveApiErrorMessage } from '@/apis/base'
import { createWorkspaceDirectory, getWorkspaceTree } from '@/apis/workspace_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  initialPath: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  includeUnboundProjectDirs: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'select'])

const columns = ref([])
const selectedPath = ref('/')
const loading = ref(false)
const error = ref('')
const showHidden = ref(false)
const editingPath = ref(false)
const pathDraft = ref('')
const exactDraftPath = ref('')
const pathInput = ref(null)
const directoryContent = ref(null)
const folderDialogOpen = ref(false)
const creatingFolder = ref(false)
const folderName = ref('')
const folderError = ref('')
const folderInput = ref(null)
let requestVersion = 0
let searchTimer = null

/** 转为个人空间内的绝对虚拟路径。 */
const normalizePath = (path) => {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts.length ? `/${parts.join('/')}` : '/'
}

/** 返回个人空间路径的父目录。 */
const parentPath = (path) => {
  const parts = normalizePath(path).split('/').filter(Boolean)
  parts.pop()
  return parts.length ? `/${parts.join('/')}` : '/'
}

const breadcrumbs = computed(() => {
  const items = [{ name: '主目录', path: '/' }]
  let path = ''
  for (const name of selectedPath.value.split('/').filter(Boolean)) {
    path += `/${name}`
    items.push({ name, path })
  }
  return items
})
const currentDirectoryName = computed(() => breadcrumbs.value.at(-1)?.name || '主目录')
const highlightedPath = computed(() => (editingPath.value ? exactDraftPath.value : selectedPath.value))
const canOpen = computed(
  () => !props.disabled && !loading.value && !error.value && !editingPath.value && selectedPath.value !== '/'
)

/** 隐藏目录只影响展示，后端仍负责权限和路径校验。 */
const visibleEntries = (entries) =>
  showHidden.value ? entries : entries.filter((entry) => !entry.name.startsWith('.'))

/** 获取工作区中可选择的直接子目录。 */
const listDirectories = async (path) => {
  const response = await getWorkspaceTree(path, false, false, props.includeUnboundProjectDirs)
  return (Array.isArray(response?.entries) ? response.entries : []).filter((entry) => entry.is_dir)
}

/** 展示目标及其父级，父级条目同时证明目标确实存在。 */
const browse = async (path) => {
  clearTimeout(searchTimer)
  const version = ++requestVersion
  const target = normalizePath(path)
  editingPath.value = false
  loading.value = true
  error.value = ''
  try {
    if (target === '/') {
      const entries = await listDirectories('/')
      if (version !== requestVersion) return
      columns.value = [{ path: '/', entries }]
    } else {
      const parent = parentPath(target)
      const [siblings, children] = await Promise.all([listDirectories(parent), listDirectories(target)])
      if (version !== requestVersion) return
      if (!siblings.some((entry) => normalizePath(entry.path) === target)) {
        const entries = await listDirectories('/')
        if (version !== requestVersion) return
        selectedPath.value = '/'
        columns.value = [{ path: '/', entries }]
        return
      }
      if (target.split('/').some((part) => part.startsWith('.'))) showHidden.value = true
      columns.value = [
        { path: parent, entries: siblings },
        { path: target, entries: children }
      ]
    }
    selectedPath.value = target
  } catch (cause) {
    if (version !== requestVersion) return
    selectedPath.value = '/'
    error.value = resolveApiErrorMessage(cause, '目录加载失败')
  } finally {
    if (version === requestVersion) {
      loading.value = false
      await nextTick()
      if (version === requestVersion) {
        directoryContent.value?.querySelector?.('.directory-row.selected')?.scrollIntoView?.({ block: 'nearest' })
      }
    }
  }
}

/** 按已返回的逐级目录匹配输入，未命中时显示主目录。 */
const searchPath = async () => {
  const version = ++requestVersion
  const raw = pathDraft.value.trim()
  const parts = raw.split('/').filter(Boolean)
  const trailingSlash = raw.endsWith('/')
  loading.value = true
  error.value = ''
  exactDraftPath.value = ''
  try {
    const rootEntries = await listDirectories('/')
    let entries = rootEntries
    let path = '/'
    let previous = null
    const completeParts = trailingSlash ? parts : parts.slice(0, -1)
    for (const part of completeParts) {
      const match = visibleEntries(entries).find((entry) => entry.name === part)
      if (!match) {
        if (version === requestVersion) columns.value = [{ path: '/', entries: rootEntries }]
        return
      }
      previous = { path, entries }
      path = normalizePath(match.path)
      entries = await listDirectories(path)
      if (version !== requestVersion) return
    }
    if (version !== requestVersion) return
    if (!parts.length) {
      exactDraftPath.value = '/'
      columns.value = [{ path: '/', entries: rootEntries }]
      return
    }
    if (trailingSlash && previous) {
      exactDraftPath.value = path
      columns.value = [previous, { path, entries }]
    } else {
      const query = parts.at(-1) || ''
      const matches = visibleEntries(entries).filter((entry) =>
        entry.name.toLocaleLowerCase().startsWith(query.toLocaleLowerCase())
      )
      if (!matches.length && query) {
        columns.value = [{ path: '/', entries: rootEntries }]
        return
      }
      const exact = matches.find((entry) => entry.name === query)
      exactDraftPath.value = exact ? normalizePath(exact.path) : ''
      columns.value = [{ path, entries: matches }]
    }
  } catch (cause) {
    if (version !== requestVersion) return
    error.value = resolveApiErrorMessage(cause, '目录加载失败')
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

/** 输入变化立即作废旧结果，再延迟查询以减少目录请求。 */
const schedulePathSearch = () => {
  clearTimeout(searchTimer)
  requestVersion += 1
  exactDraftPath.value = ''
  loading.value = true
  searchTimer = setTimeout(() => void searchPath(), 120)
}

/** 切换到路径输入并保持当前目录作为编辑起点。 */
const startPathEditing = async () => {
  pathDraft.value = selectedPath.value === '/' ? '/' : `${selectedPath.value}/`
  exactDraftPath.value = selectedPath.value
  editingPath.value = true
  await nextTick()
  pathInput.value?.focus()
}

/** 只有与已有目录精确匹配的输入才能确认导航。 */
const commitPath = () => {
  if (!loading.value && exactDraftPath.value) {
    void browse(exactDraftPath.value)
  }
}

/** 取消编辑时恢复进入编辑前的目录。 */
const cancelPathEditing = () => void browse(selectedPath.value)

/** 切换隐藏目录，隐藏当前目录时回到主目录。 */
const toggleHidden = () => {
  if (props.disabled) return
  showHidden.value = !showHidden.value
  if (editingPath.value) schedulePathSearch()
  else if (!showHidden.value && selectedPath.value.split('/').some((part) => part.startsWith('.'))) {
    void browse('/')
  }
}

/** 打开新建文件夹弹窗并聚焦名称输入。 */
const startCreatingFolder = async () => {
  folderName.value = ''
  folderError.value = ''
  folderDialogOpen.value = true
  await nextTick()
  folderInput.value?.focus()
}

/** 取消文件夹创建。 */
const cancelCreatingFolder = () => {
  if (!creatingFolder.value) folderDialogOpen.value = false
}

/** 创建成功后读取并选中新目录，失败时保留输入供修正。 */
const createFolder = async () => {
  const name = folderName.value.trim()
  if (!name || creatingFolder.value) return
  creatingFolder.value = true
  folderError.value = ''
  try {
    const response = await createWorkspaceDirectory(selectedPath.value, name)
    if (!response?.entry?.path) throw new Error('创建结果缺少目录路径')
    folderDialogOpen.value = false
    await browse(response.entry.path)
  } catch (cause) {
    folderError.value = resolveApiErrorMessage(cause, '文件夹创建失败')
  } finally {
    creatingFolder.value = false
  }
}

/** 关闭目录选择弹窗。 */
const closeDialog = () => {
  if (!props.disabled) emit('update:open', false)
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      showHidden.value = false
      void browse(props.initialPath || '/')
    } else {
      clearTimeout(searchTimer)
      requestVersion += 1
      loading.value = false
      folderDialogOpen.value = false
    }
  }
)

onUnmounted(() => {
  clearTimeout(searchTimer)
  requestVersion += 1
})
</script>

<style lang="less">
.workspace-directory-dialog {
  .ant-modal-body {
    padding: 0;
  }

  .ant-modal-header {
    margin-bottom: 0;
    padding: 24px 24px 12px;
  }

  .ant-modal-content {
    overflow: hidden;
    padding: 0;
  }
}
</style>

<style scoped lang="less">
.directory-dialog {
  color: var(--color-text);
}

.directory-location {
  display: flex;
  min-height: 48px;
  align-items: center;
  gap: 12px;
  padding: 0 16px 12px 24px;
  border-bottom: 1px solid var(--gray-150);
}

.directory-breadcrumbs {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.directory-breadcrumbs button,
.directory-edit,
.hidden-toggle {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.directory-breadcrumbs button {
  padding: 4px 0;
}

.directory-edit {
  display: flex;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: var(--color-text-secondary);
}

.directory-edit:hover:not(:disabled),
.directory-row:hover:not(:disabled) {
  background: var(--gray-50);
}

.directory-content {
  height: clamp(300px, 48vh, 480px);
}

.directory-columns {
  display: grid;
  height: 100%;
  grid-template-columns: minmax(0, 1fr);
  padding: 16px 16px;
}

.directory-columns.two-columns {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.directory-column {
  min-width: 0;
  overflow-y: auto;
}

.directory-column + .directory-column {
  margin-left: 16px;
  padding-left: 16px;
  border-left: 1px solid var(--gray-150);
}

.directory-row {
  display: flex;
  width: 100%;
  min-width: 0;
  min-height: 38px;
  align-items: center;
  gap: 9px;
  padding: 6px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  text-align: left;
}

.directory-row.selected {
  background: var(--gray-100);
}

.directory-row.selected > svg:first-child {
  color: var(--main-color);
}

.directory-row > span {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.directory-row > svg {
  flex: 0 0 auto;
}

.directory-row:focus-visible,
.directory-edit:focus-visible,
.directory-breadcrumbs button:focus-visible,
.hidden-toggle:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}

.directory-empty,
.directory-state {
  padding: 16px 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.directory-state {
  display: flex;
  height: 100%;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.directory-error,
.folder-dialog-error {
  color: var(--color-error-700);
}

.directory-error button {
  border: 0;
  background: transparent;
  color: var(--main-color);
  cursor: pointer;
}

.directory-footer,
.directory-footer-tools,
.directory-footer-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.directory-footer {
  flex-wrap: wrap;
  justify-content: space-between;
  row-gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid var(--gray-150);
}

.directory-hint {
  flex: 0 0 100%;
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.hidden-toggle {
  padding: 6px 0;
  color: var(--color-text-secondary);
}

.directory-open:not(:disabled) {
  border-color: var(--gray-900);
  background: var(--gray-900);
  color: var(--gray-0);
}

.folder-dialog-description {
  margin-bottom: 16px;
}

.folder-dialog-error {
  margin-top: 8px;
}
</style>
