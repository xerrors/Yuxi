<template>
  <section class="project-selection" :aria-label="ariaLabel">
    <a-dropdown
      v-model:open="dropdownOpen"
      :trigger="['click']"
      placement="topLeft"
      overlay-class-name="project-selection-overlay"
    >
      <button
        type="button"
        class="project-trigger"
        :class="{ active: dropdownOpen }"
        :disabled="disabled"
        aria-haspopup="menu"
        :aria-expanded="dropdownOpen"
      >
        <FolderClosed :size="15" class="project-trigger-icon" />
        <span class="project-trigger-label" :title="currentProjectHint">{{
          currentProjectLabel
        }}</span>
      </button>

      <template #overlay>
        <div class="project-dropdown-panel">
          <template v-if="dropdownView === 'projects'">
            <label class="project-search">
              <Search :size="14" aria-hidden="true" />
              <input
                ref="projectSearchInput"
                v-model="projectQuery"
                type="search"
                placeholder="搜索项目"
                aria-label="搜索项目"
              />
            </label>

            <div class="project-option-list" aria-label="选择项目">
              <div v-if="loadingProjects" class="project-loading">
                <a-spin />
              </div>
              <template v-else>
                <button
                  v-if="allowAuto"
                  type="button"
                  class="project-option"
                  :class="{ selected: !modelValue || modelValue === AUTO_PROJECT_ID }"
                  :aria-pressed="!modelValue || modelValue === AUTO_PROJECT_ID"
                  @click="selectProject(AUTO_PROJECT_ID)"
                >
                  <span class="project-option-icon"><FolderX :size="15" /></span>
                  <span class="project-option-body">
                    <strong>不使用项目</strong>
                  </span>
                  <Check
                    v-if="!modelValue || modelValue === AUTO_PROJECT_ID"
                    :size="14"
                    class="project-option-check"
                  />
                </button>

                <button
                  v-for="project in filteredProjects"
                  :key="project.id"
                  type="button"
                  class="project-option"
                  :class="{ selected: modelValue === project.id }"
                  :aria-pressed="modelValue === project.id"
                  @click="selectProject(project.id)"
                >
                  <span class="project-option-icon"><FolderClosed :size="15" /></span>
                  <span class="project-option-body">
                    <strong :title="project.name">{{ project.name }}</strong>
                  </span>
                  <Check v-if="modelValue === project.id" :size="14" class="project-option-check" />
                </button>

                <div v-if="!filteredProjects.length" class="project-empty">
                  {{ projectQuery ? '没有匹配的项目' : '暂无已有项目' }}
                </div>
              </template>
            </div>

            <div v-if="projectsError" class="project-error" role="alert">
              <span>{{ projectsError }}</span>
              <button type="button" @click="loadProjects">重新加载</button>
            </div>

            <div class="project-dropdown-actions">
              <button type="button" @click="openCreateModal()">
                <FolderPlus :size="14" />
                <span>新建项目</span>
              </button>
              <button type="button" @click="openHistoryView">
                <History :size="14" />
                <span>从历史对话添加</span>
                <ChevronRight :size="14" class="project-action-chevron" />
              </button>
            </div>
          </template>

          <template v-else>
            <div class="history-search-row">
              <button type="button" aria-label="返回项目列表" @click="closeHistoryView">
                <ArrowLeft :size="15" />
              </button>
              <label class="project-search">
                <Search :size="14" aria-hidden="true" />
                <input
                  ref="historySearchInput"
                  v-model="historyQuery"
                  type="search"
                  placeholder="搜索历史对话"
                  aria-label="搜索历史对话"
                  @input="handleHistorySearchChange"
                />
              </label>
            </div>
            <div class="history-option-list" aria-label="选择历史对话">
              <div v-if="loadingHistory" class="history-loading">
                <a-spin />
              </div>
              <template v-else>
                <button
                  v-for="candidate in historyCandidates"
                  :key="candidate.thread_id"
                  type="button"
                  class="history-option"
                  @click="selectHistoryDirectory(candidate)"
                >
                  <MessageSquare :size="14" class="history-option-icon" />
                  <span :title="candidate.title">{{ candidate.title || '未命名对话' }}</span>
                  <time
                    v-if="formatRelativeTime(candidate.updated_at)"
                    :datetime="candidate.updated_at"
                  >
                    {{ formatRelativeTime(candidate.updated_at) }}
                  </time>
                </button>
                <div v-if="!historyCandidates.length" class="history-empty">
                  {{ historyError || '没有可添加的历史对话' }}
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>
    </a-dropdown>
  </section>

  <a-modal
    v-model:open="createModalOpen"
    title="新建项目"
    width="640px"
    ok-text="创建并选择"
    cancel-text="取消"
    :confirm-loading="creatingProject"
    :ok-button-props="{ disabled: !canCreateProject }"
    @ok="handleCreateProject"
  >
    <div class="project-form">
      <label class="project-form-field">
        <span>项目名称</span>
        <a-input
          v-model:value="projectName"
          :maxlength="100"
          autofocus
          placeholder="例如：产品发布计划"
          @press-enter="canCreateProject && handleCreateProject()"
        />
      </label>

      <div class="project-form-field">
        <span>项目目录</span>
        <WorkspacePathPicker
          v-model="linkedPath"
          :active="createModalOpen"
          :disabled="creatingProject"
          include-unbound-project-dirs
        />
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { storeToRefs } from 'pinia'
import {
  ArrowLeft,
  Check,
  ChevronRight,
  FolderClosed,
  FolderPlus,
  FolderX,
  History,
  MessageSquare,
  Search
} from '@lucide/vue'
import { projectApi } from '@/apis/project_api'
import WorkspacePathPicker from '@/components/WorkspacePathPicker.vue'
import { useProjectsStore } from '@/stores/projects'
import { AUTO_PROJECT_ID, filterProjects, formatRelativeTime } from '@/utils/projectSelection'

const props = defineProps({
  modelValue: { type: String, default: AUTO_PROJECT_ID },
  disabled: { type: Boolean, default: false },
  allowAuto: { type: Boolean, default: true },
  eagerLoad: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '新对话项目' }
})
const emit = defineEmits(['update:modelValue'])
const projectsStore = useProjectsStore()
const { projects, isLoading: loadingProjects, error: projectsError } = storeToRefs(projectsStore)

const dropdownOpen = ref(false)
const projectQuery = ref('')
const projectSearchInput = ref(null)
const historySearchInput = ref(null)
const dropdownView = ref('projects')
const createModalOpen = ref(false)
const creatingProject = ref(false)
const projectName = ref('')
const projectCreationRequestId = ref('')
const linkedPath = ref('')
const historyQuery = ref('')
const historyCandidates = ref([])
const loadingHistory = ref(false)
const historyError = ref('')
let historySearchTimer = null
let projectSearchFocusTimer = null
let historyRequestVersion = 0

const requestId = () =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

const getErrorMessage = (error, fallback) =>
  error?.response?.data?.detail || error?.message || fallback

const canCreateProject = computed(() => Boolean(projectName.value.trim() && linkedPath.value))
const isAutoOrEmpty = computed(() => !props.modelValue || props.modelValue === AUTO_PROJECT_ID)
const currentProject = computed(() =>
  projects.value.find((project) => project.id === props.modelValue)
)
const currentProjectLabel = computed(() =>
  isAutoOrEmpty.value ? '选择项目' : currentProject.value?.name || '未命名项目'
)
const currentProjectHint = computed(() => {
  if (isAutoOrEmpty.value) {
    return props.allowAuto ? '不使用项目（发送时创建独立目录）' : '请选择任务使用的项目'
  }
  if (currentProject.value?.directory_mode === 'linked') return '个人空间已有目录'
  return '系统管理目录'
})
const filteredProjects = computed(() => filterProjects(projects.value, projectQuery.value))

const selectProject = (projectId) => {
  emit('update:modelValue', projectId)
  dropdownOpen.value = false
}

const addAndSelectProject = (project) => {
  const projectId = project.id
  if (!projectId) throw new Error('创建结果缺少 project id')
  projectsStore.upsertProject(project)
  selectProject(projectId)
}

const loadProjects = async () => {
  try {
    await projectsStore.loadProjects()
  } catch {
    // 错误由 store 保持，列表中的提示和重试按钮共用同一事实。
  }
}

const openCreateModal = (selectedPath = '') => {
  dropdownOpen.value = false
  projectName.value = ''
  projectCreationRequestId.value = requestId()
  linkedPath.value = selectedPath
  createModalOpen.value = true
}

const handleCreateProject = async () => {
  if (!canCreateProject.value) return
  creatingProject.value = true
  try {
    const project = await projectApi.createProject({
      requestId: projectCreationRequestId.value,
      name: projectName.value.trim(),
      mode: 'linked',
      path: linkedPath.value
    })
    addAndSelectProject(project)
    createModalOpen.value = false
    message.success('项目已创建')
  } catch (error) {
    message.error(getErrorMessage(error, '项目创建失败'))
  } finally {
    creatingProject.value = false
  }
}

const openHistoryView = () => {
  dropdownView.value = 'history'
  historyQuery.value = ''
  void loadHistoryCandidates()
}

const closeHistoryView = () => {
  dropdownView.value = 'projects'
  historyQuery.value = ''
}

const loadHistoryCandidates = async () => {
  const requestVersion = ++historyRequestVersion
  const requestedQuery = historyQuery.value.trim()
  loadingHistory.value = true
  historyError.value = ''
  try {
    const response = await projectApi.getHistoryCandidates({ query: requestedQuery, limit: 20 })
    if (requestVersion !== historyRequestVersion) return
    historyCandidates.value = response.items
  } catch (error) {
    if (requestVersion !== historyRequestVersion) return
    historyCandidates.value = []
    historyError.value = getErrorMessage(error, '历史对话加载失败')
  } finally {
    if (requestVersion === historyRequestVersion) loadingHistory.value = false
  }
}

const handleHistorySearchChange = () => {
  historyRequestVersion += 1
  loadingHistory.value = false
  if (historySearchTimer) clearTimeout(historySearchTimer)
  historySearchTimer = setTimeout(() => void loadHistoryCandidates(), 250)
}

const selectHistoryDirectory = (candidate) => {
  const path = candidate.workdir_path.replace(/^\/+|\/+$/g, '')
  const workdirPath = path ? `/${path}` : ''
  if (!workdirPath) {
    message.error('该历史对话没有可用目录')
    return
  }
  openCreateModal(workdirPath)
}

watch(dropdownOpen, (open) => {
  if (projectSearchFocusTimer) clearTimeout(projectSearchFocusTimer)
  if (!open) {
    projectQuery.value = ''
    dropdownView.value = 'projects'
    historyQuery.value = ''
    return
  }
  void loadProjects()
  projectSearchFocusTimer = setTimeout(() => projectSearchInput.value?.focus(), 120)
})
watch(dropdownView, (view) => {
  if (!dropdownOpen.value) return
  if (projectSearchFocusTimer) clearTimeout(projectSearchFocusTimer)
  const target = view === 'history' ? historySearchInput : projectSearchInput
  projectSearchFocusTimer = setTimeout(() => target.value?.focus(), 120)
})
onMounted(() => {
  if (props.eagerLoad) void loadProjects()
})
onUnmounted(() => {
  if (historySearchTimer) clearTimeout(historySearchTimer)
  if (projectSearchFocusTimer) clearTimeout(projectSearchFocusTimer)
})
</script>

<style scoped lang="less">
.project-selection {
  display: inline-flex;
  min-width: 0;
  text-align: left;
}

.project-trigger {
  display: flex;
  align-items: center;
  gap: 7px;
  max-width: min(280px, calc(100vw - 64px));
  height: 28px;
  padding: 0 6px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
  text-align: left;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;
}

.project-trigger:hover:not(:disabled),
.project-trigger.active:not(:disabled) {
  background: var(--gray-100);
  color: var(--gray-900);
}

.project-trigger:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}

.project-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.project-trigger-icon {
  flex-shrink: 0;
  color: var(--gray-500);
  transition: color 0.15s ease;
}

.project-trigger:hover:not(:disabled) .project-trigger-icon,
.project-trigger.active:not(:disabled) .project-trigger-icon {
  color: var(--gray-800);
}

.project-trigger-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 400;
  line-height: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-dropdown-panel {
  width: min(300px, calc(100vw - 24px));
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-0);
  box-shadow: 0 8px 24px var(--shadow-4);
}

.project-search {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 6px 6px 0;
  padding: 0 6px;
  color: var(--color-text-tertiary);
}

.project-search input {
  width: 100%;
  min-width: 0;
  height: 28px;
  padding: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  font-size: 12.5px;
}

.project-search input::placeholder {
  color: var(--color-text-tertiary);
}

.project-option-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-height: 120px;
  max-height: min(260px, calc(100vh - 220px));
  overflow-y: auto;
  border-bottom: 1px solid var(--gray-100);
  padding: 4px 6px;
}

.project-loading,
.history-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  width: 100%;
}

.project-option {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  width: 100%;
  min-height: 28px;
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  text-align: left;
  transition: background-color 0.12s ease;
}

.project-option:hover:not(:disabled) {
  background: var(--gray-50);
}

.project-option.selected {
  background: var(--gray-50);
}

.project-option:focus-visible,
.history-option:focus-visible,
.history-search-row > button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}

.project-option:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.project-option-icon {
  display: inline-flex;
  flex: 0 0 auto;
  color: var(--color-text-secondary);
}

.project-option.selected .project-option-icon,
.project-option.selected > svg {
  color: var(--main-color);
}

.project-option-body {
  flex: 1;
  min-width: 0;
}

.project-option-body strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  font-weight: 400;
}

.project-option-check {
  flex: 0 0 auto;
  color: var(--main-color);
}

.project-dropdown-actions {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 4px 6px;
}

.project-dropdown-actions button {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: var(--gray-0);
  color: var(--color-text);
  cursor: pointer;
  font-size: 12.5px;
  text-align: left;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;
}

.project-dropdown-actions button:hover {
  background: var(--gray-50);
  color: var(--gray-1000);
}

.project-action-chevron {
  margin-left: auto;
  color: var(--color-text-tertiary);
}

.history-search-row {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 6px;
}

.history-search-row > button {
  display: inline-flex;
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.history-search-row > button:hover {
  background: var(--gray-50);
  color: var(--color-text);
}

.history-search-row .project-search {
  flex: 1;
  min-width: 0;
  margin: 0;
}

.project-empty {
  padding: 14px 8px 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-align: center;
}

.project-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 6px 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--color-error-50);
  color: var(--color-error-700);
  font-size: 12px;
}

.project-error button {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-weight: 600;
}

.project-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.project-form-field {
  display: flex;
  flex-direction: column;
  gap: 7px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
}

.history-option-list {
  display: flex;
  min-height: 140px;
  max-height: 240px;
  flex-direction: column;
  overflow-y: auto;
  border-top: 1px solid var(--gray-100);
  background: var(--gray-0);
  padding: 2px 4px;
}

.history-option {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
  min-height: 28px;
  padding: 4px 6px;
  border: 0;
  border-bottom: 1px solid var(--gray-100);
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  text-align: left;
}

.history-option:last-child {
  border-bottom: 0;
}

.history-option:hover:not(:disabled) {
  background: var(--gray-50);
}

.history-option:disabled {
  cursor: wait;
  opacity: 0.55;
}

.history-option > span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
}

.history-option-icon {
  flex: 0 0 auto;
  color: var(--color-text-tertiary);
}

.history-option time {
  flex: 0 0 auto;
  color: var(--color-text-tertiary);
  font-size: 11px;
  white-space: nowrap;
}

.history-empty {
  margin: auto;
  padding: 16px;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-align: center;
}

@media (max-width: 768px) {
  .project-trigger {
    max-width: calc(100vw - 72px);
  }
}

@media (max-width: 520px) {
  .project-dropdown-panel {
    width: min(270px, calc(100vw - 105px));
  }
}
</style>
