<template>
  <section
    v-if="normalizedArtifacts.length"
    class="artifacts-list"
    :class="{ 'is-box-group': hiddenArtifactCount > 0 }"
    aria-label="交付物"
  >
    <div v-if="hiddenArtifactCount > 0" class="artifacts-heading">
      <span
        >交付物 <span class="artifact-count">{{ normalizedArtifacts.length }}</span></span
      >
      <button
        type="button"
        class="lucide-icon-btn artifacts-toggle"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : `展开其余 ${hiddenArtifactCount} 个` }}
        <ChevronDown :size="14" :class="{ 'is-expanded': expanded }" aria-hidden="true" />
      </button>
    </div>
    <TransitionGroup name="artifact-reveal" tag="div" class="artifact-items">
      <div v-for="file in visibleArtifacts" :key="file.path" class="artifact-entry">
        <div class="artifact-card">
          <button
            type="button"
            class="item-main"
            :title="`打开 ${file.name}`"
            @click="openPreview(file)"
          >
            <FileTypeIcon :name="file.path" :size="20" class="item-icon" />
            <span class="item-name">{{ file.name }}</span>
          </button>
          <ActionDropdown
            :open="openActionPath === file.path"
            align="end"
            :width="168"
            @update:open="openActionPath = $event ? file.path : null"
          >
            <template #trigger="{ open }">
              <button
                type="button"
                class="item-action-btn"
                title="更多操作"
                :aria-label="`更多操作：${file.name}`"
                aria-haspopup="menu"
                :aria-expanded="open"
                @keydown.down.prevent="$event.currentTarget.click()"
              >
                <Ellipsis :size="18" aria-hidden="true" />
              </button>
            </template>
            <template #default="{ closeAndFocus }">
              <div
                role="menu"
                :aria-label="`更多操作：${file.name}`"
                @keydown.up.prevent="moveActionFocus"
                @keydown.down.prevent="moveActionFocus"
                @keydown.tab="closeAndFocus"
              >
                <button type="button" role="menuitem" class="config-dropdown-item artifact-menu-item" @click="closeAndFocus(); downloadFile(file)">
                  <Download :size="14" aria-hidden="true" />
                  下载
                </button>
                <button
                  type="button"
                  role="menuitem"
                  class="config-dropdown-item artifact-menu-item"
                  :disabled="isSaving(file.path)"
                  @click="closeAndFocus(); saveToWorkspace(file)"
                >
                  <Save :size="14" aria-hidden="true" />
                  {{ isSaving(file.path) ? '保存中' : '保存到个人空间' }}
                </button>
              </div>
            </template>
          </ActionDropdown>
        </div>
      </div>
    </TransitionGroup>
  </section>

  <a-modal
    :open="saveDialogOpen"
    title="保存交付物"
    ok-text="保存"
    cancel-text="取消"
    :ok-button-props="{ disabled: !selectedDestination || pickerLoading }"
    :confirm-loading="pendingSaveFile ? isSaving(pendingSaveFile.path) : false"
    @ok="confirmSave"
    @cancel="closeSaveDialog"
  >
    <p class="save-dialog-hint">选择保存到个人工作区的目录</p>
    <WorkspacePathPicker
      v-model="selectedDestination"
      selection-mode="directory"
      :active="saveDialogOpen"
      :disabled="pendingSaveFile ? isSaving(pendingSaveFile.path) : false"
      @loading-change="pickerLoading = $event"
    />
  </a-modal>
</template>

<script setup>
import { computed, ref } from 'vue'
import {
  buildArtifactItems,
  visibleArtifactItems
} from '@/modules/session/model/artifactPresentation'
import { message } from 'ant-design-vue'
import { ChevronDown, Download, Ellipsis, Save } from '@lucide/vue'
import { threadApi } from '@/apis/agent_api'
import ActionDropdown from '@/shared/ui/ActionDropdown.vue'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import WorkspacePathPicker from '@/modules/workspace/ui/WorkspacePathPicker.vue'
import { parseDownloadFilename } from '@/shared/lib/file_utils'

const props = defineProps({
  artifacts: {
    type: Array,
    default: () => []
  },
  threadId: {
    type: String,
    default: null
  }
})
const emit = defineEmits(['saved', 'open-preview'])

const normalizedArtifacts = computed(() => buildArtifactItems(props.artifacts))
const expanded = ref(false)
const openActionPath = ref(null)
const collapsedArtifacts = computed(() => visibleArtifactItems(normalizedArtifacts.value, false))
const hiddenArtifactCount = computed(
  () => normalizedArtifacts.value.length - collapsedArtifacts.value.length
)
const visibleArtifacts = computed(() =>
  expanded.value ? normalizedArtifacts.value : collapsedArtifacts.value
)
const savingState = ref({})
const saveDialogOpen = ref(false)
const pendingSaveFile = ref(null)
const selectedDestination = ref('/saved_artifacts')
const pickerLoading = ref(false)

/** 上下键在可用的交付物菜单项间循环。 */
const moveActionFocus = (event) => {
  const items = [...event.currentTarget.querySelectorAll('button:not(:disabled)')]
  const direction = event.key === 'ArrowDown' ? 1 : -1
  const next = (items.indexOf(event.target) + direction + items.length) % items.length
  items[next]?.focus()
}

const openPreview = (file) => {
  emit('open-preview', { ...file })
}

const downloadFile = async (file) => {
  if (!props.threadId || !file?.path) return

  try {
    const response = await threadApi.downloadThreadArtifact(props.threadId, file.path)
    const blob = await response.blob()
    const contentDisposition =
      response.headers.get('Content-Disposition') || response.headers.get('content-disposition')
    const filename = parseDownloadFilename(contentDisposition) || file.name
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    message.error(error?.message || '下载文件失败')
  }
}

const isSaving = (path) => !!savingState.value[path]

const setSaving = (path, saving) => {
  savingState.value = {
    ...savingState.value,
    [path]: saving
  }
}

const saveToWorkspace = (file) => {
  if (!props.threadId || !file?.path || isSaving(file.path)) {
    return
  }
  pendingSaveFile.value = file
  selectedDestination.value = '/saved_artifacts'
  saveDialogOpen.value = true
}

const closeSaveDialog = () => {
  if (pendingSaveFile.value && isSaving(pendingSaveFile.value.path)) return
  saveDialogOpen.value = false
  pendingSaveFile.value = null
}

const confirmSave = async () => {
  const file = pendingSaveFile.value
  if (!props.threadId || !file?.path || !selectedDestination.value || isSaving(file.path)) return

  setSaving(file.path, true)
  try {
    const result = await threadApi.saveThreadArtifactToWorkspace(
      props.threadId,
      file.path,
      selectedDestination.value
    )
    message.success(`已保存到个人空间：${result.saved_path}`)
    emit('saved', result)
    saveDialogOpen.value = false
    pendingSaveFile.value = null
  } catch (error) {
    message.error(error?.message || '保存到个人空间失败')
  } finally {
    setSaving(file.path, false)
  }
}
</script>

<style scoped lang="less">
.save-dialog-hint {
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.artifacts-list {
  width: 100%;
  margin: 8px 0 4px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.artifacts-list.is-box-group {
  gap: 4px;
  padding: 8px;
  border-radius: 12px;
  background: var(--gray-25);
}

.artifact-items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.artifact-entry {
  display: grid;
  grid-template-rows: 1fr;

  .artifact-card {
    min-height: 0;
    overflow: hidden;
  }
}

.artifact-reveal-enter-active,
.artifact-reveal-leave-active {
  transition:
    grid-template-rows 0.24s ease,
    opacity 0.2s ease;
}

.artifact-reveal-enter-from,
.artifact-reveal-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}

.artifacts-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 4px 4px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
}

.artifact-count {
  margin-left: 4px;
  color: var(--color-text-secondary);
  font-weight: 400;
}

.artifacts-toggle {
  gap: 4px;
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;

  &:hover {
    background: var(--gray-100);
    color: var(--color-text);
  }

  svg {
    transition: transform 0.24s ease;
  }

  .is-expanded {
    transform: rotate(180deg);
  }
}

.artifacts-toggle:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}

.artifact-card {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  border-radius: 12px;
  background: var(--gray-50);
  transition: background-color 0.18s ease;

  &:hover {
    background: var(--gray-100);
  }
}

.item-main {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  border: none;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  padding: 10px 8px 10px 12px;
}

.item-main:focus-visible,
.item-action-btn:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}

.item-icon {
  flex-shrink: 0;
  font-size: 18px;
  opacity: 0.86;
}

.item-name {
  min-width: 0;
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: var(--gray-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.3;
}

.item-action-btn {
  flex-shrink: 0;
  align-self: center;
  width: 30px;
  height: 30px;
  margin-right: 8px;
  border: none;
  background: transparent;
  color: var(--gray-600);
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition:
    background-color 0.18s ease,
    color 0.18s ease;
}

.item-action-btn:hover,
.item-action-btn[aria-expanded="true"] {
  color: var(--main-700);
  background: var(--gray-200);
}

.artifact-menu-item:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}

@media (max-width: 768px) {
  .artifacts-list {
    margin-top: 6px;
  }

  .artifact-card {
    align-items: stretch;
  }

  .item-main {
    padding: 9px 6px 9px 12px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .artifact-reveal-enter-active,
  .artifact-reveal-leave-active,
  .artifacts-toggle svg {
    transition: none;
  }
}
</style>
