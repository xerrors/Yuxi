<template>
  <ExtensionDetailLayout
    :active-key="activeTab"
    @update:active-key="changeTab"
    :tabs="skillDetailTabs"
    :loading="loading"
    :ready="Boolean(currentSkill && isInstalledSkill)"
    empty-description="未找到 Skill"
    class="skill-detail"
  >
    <template #breadcrumb>
      <nav class="extension-detail-breadcrumb" aria-label="技能详情导航">
        <button type="button" class="extension-detail-back" @click="goBack">
          {{ route.query.agent ? '智能体' : '技能' }}
        </button>
        <ChevronRight :size="15" aria-hidden="true" />
        <span class="extension-detail-current" :title="currentSkill?.name || slug">
          {{ currentSkill?.name || slug }}
        </span>
      </nav>
    </template>
    <template #actions>
      <div class="extension-detail-actions">
        <div class="detail-actions">
          <a-space :size="8">
            <span v-if="hasContentChanges" class="readonly-scope-hint">未保存</span>
            <a-button
              v-if="activeTab === 'editor' && canEditSkill && selectedPath && !selectedIsDir"
              type="text"
              class="lucide-icon-btn extension-detail-action"
              :disabled="savingAny || pendingFileLoad"
              aria-label="编辑当前文件"
              title="编辑当前文件"
              @click="startEditingCurrentFile"
            >
              <FilePen :size="14" />
              <span>编辑</span>
            </a-button>
            <a-button
              v-if="activeTab === 'editor'"
              type="text"
              class="lucide-icon-btn extension-detail-action tree-toggle"
              :class="{ active: treeVisible }"
              :aria-expanded="treeVisible"
              aria-controls="skill-project-tree"
              :title="treeVisible ? '隐藏项目结构' : '显示项目结构'"
              @click="treeVisible = !treeVisible"
              :aria-label="treeVisible ? '隐藏项目结构' : '显示项目结构'"
            >
              <FolderTree :size="14" aria-hidden="true" />
              <span>文件</span>
            </a-button>
            <a-button
              v-if="isInstalledSkill && canManageCurrentSkill"
              type="text"
              aria-label="导出 Skill"
              title="导出 Skill"
              @click="handleExport"
              class="lucide-icon-btn extension-detail-action"
            >
              <Download :size="14" />
              <span>导出</span>
            </a-button>
            <a-button
              v-if="
                isInstalledSkill &&
                canManageCurrentSkill &&
                !isBuiltinInstalledSkill &&
                !isAgentBoundSkill
              "
              type="text"
              danger
              aria-label="删除 Skill"
              title="删除 Skill"
              @click="confirmDeleteSkill"
              class="lucide-icon-btn extension-detail-action"
            >
              <Trash2 :size="14" />
              <span>删除</span>
            </a-button>
          </a-space>
        </div>
      </div>
    </template>

    <template #panel-editor>
      <div class="editor-tab-content">
        <div v-if="isReadOnlySkill" class="readonly-scope-hint readonly-detail-hint">
          你可以查看并使用此 Skill，但没有管理权限。
        </div>
        <div class="workspace" :class="{ 'tree-visible': treeVisible }">
          <Transition name="skill-tree">
            <div v-if="treeVisible" id="skill-project-tree" class="tree-container">
              <div class="tree-header">
                <span class="label">项目结构</span>
                <div class="tree-actions">
                  <a-tooltip
                    v-if="canEditSkill && selectedPath && !selectedIsDir"
                    title="编辑当前文件"
                  >
                    <button
                      type="button"
                      aria-label="编辑当前文件"
                      :disabled="savingAny || pendingFileLoad"
                      @click="startEditingCurrentFile"
                    >
                      <FilePen :size="14" />
                    </button>
                  </a-tooltip>
                  <a-tooltip v-if="canEditSkill" title="新建文件">
                    <button
                      type="button"
                      :disabled="savingAny || pendingFileLoad"
                      aria-label="新建文件"
                      @click="openCreateModal(false)"
                    >
                      <FilePlus :size="14" />
                    </button>
                  </a-tooltip>
                  <a-tooltip v-if="canEditSkill" title="新建目录">
                    <button
                      type="button"
                      :disabled="savingAny || pendingFileLoad"
                      aria-label="新建目录"
                      @click="openCreateModal(true)"
                    >
                      <FolderPlus :size="14" />
                    </button>
                  </a-tooltip>
                  <a-tooltip title="刷新">
                    <button
                      type="button"
                      :disabled="savingAny || pendingFileLoad"
                      aria-label="刷新项目结构"
                      @click="reloadTree"
                    >
                      <RotateCw :size="14" />
                    </button>
                  </a-tooltip>
                </div>
              </div>
              <div class="tree-content">
                <FileTreeComponent
                  v-model:selectedKeys="selectedTreeKeys"
                  v-model:expandedKeys="expandedKeys"
                  :tree-data="treeData"
                  @select="handleTreeSelect"
                />
              </div>
            </div>
          </Transition>
          <div class="editor-container">
            <div class="skill-editor-toolbar">
              <span class="skill-editor-path">{{ selectedPath || '文件' }}</span>
              <a-button
                v-if="canEditSkill && hasContentChanges"
                size="small"
                type="primary"
                :loading="savingAny"
                :disabled="pendingFileLoad"
                title="保存全部文件与依赖修改"
                @click="saveContent"
              >保存修改</a-button>
            </div>
            <div class="editor-main">
              <a-empty
                v-if="!selectedPath || selectedIsDir"
                description="选择文件以开始编辑"
                class="mt-40"
              />
              <template v-else>
                <AgentFilePreview
                  ref="filePreviewRef"
                  :file="selectedFilePreview"
                  :file-path="selectedPath"
                  :show-header="false"
                  :show-download="false"
                  :show-save-action="false"
                  :show-inline-html-controls="true"
                  :borderless="true"
                  :editable="canEditSkill && !pendingFileLoad"
                  :edit-all-text="true"
                  :saving="savingAny"
                  :draft="draftFiles[selectedPath]"
                  @draft-change="updateDraftFile"
                  :full-height="true"
                  container-class="skill-file-preview"
                  content-class="skill-file-preview-content"
                  @save="saveCurrentFile"
                />
              </template>
            </div>
          </div>
        </div>
      </div>
    </template>

    <template #panel-versions>
      <SkillVersionPanel
        v-if="isAgentBoundSkill && activeTab === 'versions'"
        :slug="slug"
        :can-manage="canManageCurrentSkill"
        :can-restore="!hasContentChanges && !savingAny"
        :can-release="!hasContentChanges && !savingAny"
        @restoring="restoringVersion = $event"
        @restored="fetchSkillDetail"
      />
    </template>

    <template #panel-config>
      <div class="extension-detail-view extension-detail-gray-switches config-view">
        <a-alert
          v-if="isAgentBoundSkill"
          type="info"
          message="专属 Skill 自动加载，可用范围与管理权限跟随所属智能体。"
          :show-icon="true"
        />
        <section v-if="!isAgentBoundSkill" class="config-section extension-detail-section">
          <div class="config-section-header extension-detail-section-header">
            <div class="text extension-detail-section-heading">
              <h3>可用范围</h3>
              <p>决定此 Skill 是否可被选择，以及哪些用户可在运行时使用它。</p>
            </div>
            <a-button
              v-if="canManageCurrentSkill && (hasUnsavedShareConfigChanges || savingShareConfig)"
              type="primary"
              :loading="savingShareConfig"
              @click="saveShareConfig"
            >
              保存范围
            </a-button>
          </div>
          <div class="settings-stack extension-detail-divider-list config-section-body">
            <section class="settings-card extension-detail-divider-row">
              <div class="settings-card-main">
                <div class="settings-card-title">启用状态</div>
                <div class="settings-card-desc">
                  禁用后此 Skill 不会出现在可选资源中，也不会参与 Agent 运行时加载。
                </div>
              </div>
              <div class="settings-card-action">
                <span class="status-pill" :class="enabledForm ? 'enabled' : 'disabled'">
                  {{ enabledForm ? '已启用' : '已禁用' }}
                </span>
                <a-switch
                  v-model:checked="enabledForm"
                  size="small"
                  :aria-label="`启用状态${enabledForm ? '已启用' : '已禁用'}`"
                  :disabled="!canManageCurrentSkill"
                />
              </div>
            </section>

            <section class="settings-card scope-card extension-detail-divider-row">
              <div class="settings-card-main">
                <div class="settings-card-title">共享范围</div>
                <div class="settings-card-desc">选择哪些用户可以发现并使用此 Skill。</div>
              </div>
              <div v-if="isBuiltinInstalledSkill" class="readonly-scope-hint">
                内置 Skill 固定为全局生效范围，可通过启用状态控制是否参与运行时。
              </div>
              <div v-else-if="isReadOnlySkill" class="readonly-scope-hint">
                当前 Skill 对你只读，不能修改生效范围。
              </div>
              <template v-else>
                <a-alert
                  v-if="currentSkill?.share_config_invalid"
                  type="warning"
                  show-icon
                  role="alert"
                  message="共享配置无效。已暂设为仅所有者，请检查共享范围并保存。"
                />
                <ShareConfigForm
                  ref="shareConfigFormRef"
                  v-model="shareConfigForm"
                  :auto-select-user-dept="true"
                  :allowed-access-levels="allowedSkillAccessLevels"
                />
              </template>
            </section>
          </div>
        </section>

        <section class="config-section extension-detail-section">
          <div class="config-section-header extension-detail-section-header">
            <div class="text extension-detail-section-heading">
              <h3>运行依赖</h3>
              <p>声明所需依赖；连接器在此 Skill 激活后按需加载。</p>
            </div>
            <a-button v-if="canEditSkill && hasContentChanges" type="primary"
              :loading="savingAny" :disabled="pendingFileLoad" @click="saveContent"
              title="保存全部文件与依赖修改">保存修改</a-button>
          </div>
          <div class="dependency-groups extension-detail-divider-list config-section-body">
            <section
              v-for="group in dependencyGroups"
              :key="group.key"
              class="dependency-card extension-detail-divider-row"
              :class="{ readonly: !canEditSkill }"
            >
              <div class="dependency-card-header">
                <div class="dependency-title-block">
                  <div class="dependency-title-row">
                    <h4>{{ group.title }}</h4>
                    <span v-if="getDependencyValues(group).length" class="dependency-count"
                      >已选择 {{ getDependencyValues(group).length }} 项</span
                    >
                  </div>
                  <p>{{ group.description }}</p>
                </div>
                <a-dropdown
                  v-if="canEditSkill"
                  :trigger="['click']"
                  placement="bottomRight"
                  overlay-class-name="dependency-selection-popover"
                >
                  <a-button
                    size="small"
                    class="dependency-action-btn dependency-select-btn"
                    :disabled="savingAny || pendingFileLoad"
                  >
                    <Plus :size="13" />
                    <span>选择依赖</span>
                    <ChevronDown :size="12" class="dependency-select-chevron" />
                  </a-button>
                  <template #overlay>
                    <div class="selection-dropdown" @mousedown.stop @click.stop>
                      <div class="selection-dropdown-header">
                        <div class="selection-dropdown-title">{{ group.title }}</div>
                        <div class="selection-dropdown-subtitle">
                          {{ group.dropdownHint }}
                        </div>
                      </div>
                      <a-input
                        v-model:value="dependencySearch[group.key]"
                        size="small"
                        allow-clear
                        class="selection-search"
                        :placeholder="`搜索${group.shortTitle}`"
                        @mousedown.stop
                        @click.stop
                      />
                      <div v-if="getFilteredDependencyOptions(group).length" class="selection-list">
                        <div
                          v-for="option in getFilteredDependencyOptions(group)"
                          :key="option.value"
                          role="checkbox"
                          :aria-checked="isDependencySelected(group, option.value)"
                          tabindex="0"
                          class="selection-item"
                          :class="{ selected: isDependencySelected(group, option.value) }"
                          @mousedown.stop
                          @click.stop="
                            toggleDependency(
                              group,
                              option.value,
                              !isDependencySelected(group, option.value)
                            )
                          "
                          @keydown.enter.prevent="
                            toggleDependency(
                              group,
                              option.value,
                              !isDependencySelected(group, option.value)
                            )
                          "
                          @keydown.space.prevent="
                            toggleDependency(
                              group,
                              option.value,
                              !isDependencySelected(group, option.value)
                            )
                          "
                        >
                          <span class="selection-item-content">
                            <a-checkbox
                              :checked="isDependencySelected(group, option.value)"
                              :disabled="savingAny || pendingFileLoad"
                              @click.stop
                              @change="toggleDependency(group, option.value, $event.target.checked)"
                            />
                            <span class="selection-label">{{ option.label }}</span>
                          </span>
                        </div>
                      </div>
                      <div v-else class="selection-empty">
                        {{ group.options.length ? '没有匹配的依赖' : '暂无可选依赖' }}
                      </div>
                    </div>
                  </template>
                </a-dropdown>
                <a-button v-else size="small" disabled class="dependency-action-btn">
                  {{ isBuiltinInstalledSkill ? '系统维护' : '只读' }}
                </a-button>
              </div>

              <div v-if="getDependencyValues(group).length" class="dependency-chip-list">
                <span
                  v-for="value in getDependencyValues(group)"
                  :key="value"
                  class="dependency-chip"
                  :title="getDependencyOptionLabel(group, value)"
                >
                  <span>{{ getDependencyOptionLabel(group, value) }}</span>
                  <button
                    v-if="canEditSkill"
                    type="button"
                    class="dependency-chip-remove"
                    :disabled="savingAny || pendingFileLoad"
                    :aria-label="`移除 ${getDependencyOptionLabel(group, value)}`"
                    @click="removeDependency(group, value)"
                  >
                    <X :size="12" />
                  </button>
                </span>
              </div>
              <div v-else class="dependency-empty-hint">{{ group.emptyText }}</div>
            </section>
          </div>
        </section>
      </div>
    </template>

    <template #overlays>
      <a-modal
        v-model:open="createModalVisible"
        :title="createForm.isDir ? '新建目录' : '新建文件'"
        @ok="handleCreateNode"
        :confirm-loading="creatingNode"
        width="400px"
      >
        <a-form layout="vertical" class="pt-12">
          <a-form-item label="路径 (相对于根目录)" required>
            <a-input v-model:value="createForm.path" placeholder="src/main.py" />
          </a-form-item>
          <a-form-item v-if="!createForm.isDir" label="内容">
            <a-textarea v-model:value="createForm.content" :rows="5" />
          </a-form-item>
        </a-form>
      </a-modal>
    </template>
  </ExtensionDetailLayout>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  Download,
  Trash2,
  FilePen,
  FileText,
  Settings,
  FolderTree,
  FilePlus,
  FolderPlus,
  RotateCw,
  X,
  Plus,
  ChevronDown,
  ChevronRight
} from '@lucide/vue'
import { resolveApiErrorMessage } from '@/apis/base'
import { skillApi } from '@/apis/skill_api'
import {
  collectSkillChanges,
  readDraftDependencies,
  writeDraftDependencies
} from '@/modules/extensions/model/skillDraft'
import AgentFilePreview from '@/modules/session/ui/workspace/AgentFilePreview.vue'
import ExtensionDetailLayout from '@/shared/ui/ExtensionDetailLayout.vue'
import FileTreeComponent from '@/modules/session/ui/workspace/FileTreeComponent.vue'
import SkillVersionPanel from '@/modules/extensions/ui/SkillVersionPanel.vue'
import ShareConfigForm from '@/modules/agents/ui/ShareConfigForm.vue'
import { cloneShareConfig } from '@/modules/agents'

const route = useRoute()
const router = useRouter()
const slug = computed(() => decodeURIComponent(route.params.slug))

const baseSkillDetailTabs = [
  {
    key: 'editor',
    label: '文件',
    icon: FileText,
    panelClass: 'extension-detail-panel-fixed'
  },
  { key: 'config', label: '配置', icon: Settings }
]

const loading = ref(false)
const currentSkill = ref(null)
const skillDeleted = ref(false)
const treeData = ref([])
const selectedTreeKeys = ref([])
const expandedKeys = ref([])
const selectedPath = ref('')
const selectedIsDir = ref(false)
const fileContent = ref('')
const contentRevision = ref('')
const savedFiles = ref({})
const draftFiles = ref({})
const nodeChanges = ref([])
const hasContentChanges = computed(
  () => collectSkillChanges(savedFiles.value, draftFiles.value, nodeChanges.value).length > 0
)
const pendingFileLoad = ref(false)
const savingFile = ref(false)
const creatingNode = ref(false)
const savingShareConfig = ref(false)
const restoringVersion = ref(false)
const savingAny = computed(
  () => savingFile.value || savingShareConfig.value || restoringVersion.value || loading.value
)
const activeTab = ref('editor')
const treeVisible = ref(false)
const filePreviewRef = ref(null)

const skills = ref([])
const createModalVisible = ref(false)
const createForm = reactive({ path: '', isDir: false, content: '' })
const allowedSkillAccessLevels = ref(['user'])
const enabledForm = ref(true)
const shareConfigFormRef = ref(null)
const shareConfigForm = ref({
  version: 2,
  read_scope: { access_level: 'user', department_ids: [], user_uids: [] },
  manage_scope: null
})
const dependencyOptions = reactive({ tools: [], mcps: [], skills: [] })
const dependencyForm = reactive({
  tool_dependencies: [],
  mcp_dependencies: [],
  skill_dependencies: []
})
const dependencySearch = reactive({ tools: '', mcps: '', skills: '' })

const isInstalledSkill = computed(() => !!currentSkill.value?.dir_path)

const isAgentBoundSkill = computed(() => currentSkill.value?.bound_agent_id != null)
const skillDetailTabs = computed(() =>
  isAgentBoundSkill.value
    ? [...baseSkillDetailTabs, { key: 'versions', label: '历史版本', icon: RotateCw }]
    : baseSkillDetailTabs
)
const isBuiltinInstalledSkill = computed(() => {
  return !!(isInstalledSkill.value && currentSkill.value?.source_type === 'builtin')
})
const canManageCurrentSkill = computed(() => currentSkill.value?.can_manage !== false)
const isReadOnlySkill = computed(() => isInstalledSkill.value && !canManageCurrentSkill.value)
const canEditSkill = computed(() => canManageCurrentSkill.value && !isBuiltinInstalledSkill.value)
const hasUnsavedShareConfigChanges = computed(() =>
  currentSkill.value
    ? currentSkill.value.share_config_invalid ||
      enabledForm.value !== (currentSkill.value.enabled !== false) ||
      JSON.stringify(shareConfigForm.value) !==
        JSON.stringify(
          cloneShareConfig(currentSkill.value.share_config, currentSkill.value.share_config_invalid)
        )
    : false
)

const selectedFilePreview = computed(() => ({
  content: fileContent.value,
  previewType: 'text',
  supported: true,
  status: pendingFileLoad.value ? 'loading' : 'ready'
}))

const toolDependencyOptions = computed(() =>
  (dependencyOptions.tools || []).map((i) =>
    typeof i === 'object'
      ? { label: i.name || i.slug, value: i.slug || i.id }
      : { label: i, value: i }
  )
)
const mcpDependencyOptions = computed(() =>
  (dependencyOptions.mcps || []).map((i) => ({ label: i, value: i }))
)
const skillDependencyOptions = computed(() =>
  (dependencyOptions.skills || [])
    .filter((s) => s !== currentSkill.value?.slug)
    .map((i) => ({ label: i, value: i }))
)

const dependencyGroups = computed(() => [
  {
    key: 'tools',
    formKey: 'tool_dependencies',
    title: '工具依赖',
    shortTitle: '工具',
    description: '声明此 Skill 运行时需要调用的工具能力。',
    dropdownHint: '选择后 Agent 运行时会同时加载这些工具。',
    emptyText: '未声明工具依赖',
    options: toolDependencyOptions.value
  },
  {
    key: 'mcps',
    formKey: 'mcp_dependencies',
    title: '连接器依赖',
    shortTitle: '连接器',
    description: '声明此 Skill 依赖的连接器。',
    dropdownHint: '选择此 Skill 运行时需要的连接器。',
    emptyText: '未声明连接器依赖',
    options: mcpDependencyOptions.value
  },
  {
    key: 'skills',
    formKey: 'skill_dependencies',
    title: 'Skill 依赖',
    shortTitle: 'Skill',
    description: '声明需要一起加载的其他 Skill。',
    dropdownHint: '依赖 Skill 会随当前 Skill 一起进入运行时可读范围。',
    emptyText: '未声明 Skill 依赖',
    options: skillDependencyOptions.value
  }
])

const getDependencyValues = (group) => dependencyForm[group.formKey] || []

const getDependencyOptionLabel = (group, value) => {
  const option = group.options.find((item) => item.value === value)
  return option?.label || value
}

const getFilteredDependencyOptions = (group) => {
  const keyword = String(dependencySearch[group.key] || '')
    .trim()
    .toLowerCase()
  if (!keyword) return group.options
  return group.options.filter((option) => {
    const label = String(option.label || '').toLowerCase()
    const value = String(option.value || '').toLowerCase()
    return label.includes(keyword) || value.includes(keyword)
  })
}

const isDependencySelected = (group, value) => getDependencyValues(group).includes(value)

const toggleDependency = (group, value, checked) => {
  if (!canEditSkill.value || savingAny.value || pendingFileLoad.value) return
  const values = getDependencyValues(group)
  if (checked) {
    if (!values.includes(value)) dependencyForm[group.formKey] = [...values, value]
    syncDependencyDraft()
    return
  }
  dependencyForm[group.formKey] = values.filter((item) => item !== value)
  syncDependencyDraft()
}

const removeDependency = (group, value) => {
  toggleDependency(group, value, false)
}

const goBack = () => {
  if (route.query.agent)
    router.push({ name: 'AgentManageComp', query: { agent: route.query.agent } })
  else router.push({ path: '/extensions', query: { tab: 'skills' } })
}

const confirmDiscardFileDraft = (includeSettings = true) => {
  const hasFileDraft = hasContentChanges.value
  if (!hasFileDraft && !(includeSettings && hasUnsavedShareConfigChanges.value))
    return Promise.resolve(true)
  return new Promise((resolve) => {
    Modal.confirm({
      title: '放弃未保存的修改？',
      content: includeSettings ? '当前文件或配置还有未保存的修改。' : '当前文件的修改还没有保存。',
      okText: '放弃修改',
      okType: 'danger',
      cancelText: '继续编辑',
      onOk: () => {
        if (savingAny.value) {
          resolve(false)
          return
        }
        if (hasFileDraft) {
          draftFiles.value = { ...savedFiles.value }
          nodeChanges.value = []
        }
        resolve(true)
      },
      onCancel: () => resolve(false)
    })
  })
}

const changeTab = async (nextTab) => {
  if (nextTab === activeTab.value) return
  if (savingAny.value || pendingFileLoad.value) {
    message.warning('请等待当前操作完成')
    return
  }
  activeTab.value = nextTab
}

const warnBeforeUnload = (event) => {
  if (skillDeleted.value) return
  if (!savingAny.value && !hasContentChanges.value && !hasUnsavedShareConfigChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}

const startEditingCurrentFile = () => {
  if (pendingFileLoad.value) return
  filePreviewRef.value?.startEditing?.()
}

const syncShareConfigFromSkill = (skillRecord) => {
  enabledForm.value = skillRecord?.enabled !== false
  shareConfigForm.value = cloneShareConfig(
    skillRecord?.share_config,
    skillRecord?.share_config_invalid
  )
}

const fetchSkillDetail = async () => {
  loading.value = true
  try {
    const skillResult = await skillApi.listSkills()
    skills.value = skillResult?.data || []
    allowedSkillAccessLevels.value = skillResult?.allowed_access_levels || ['user']

    const found = (await skillApi.getSkillDetail(slug.value))?.data
    if (found) {
      currentSkill.value = found
      syncDependencyFormFromSkill(found)
      syncShareConfigFromSkill(found)
      await reloadContent()
      await loadSkillFile(found.slug)
    }
    await fetchDependencyOptions(currentSkill.value?.slug)
  } catch {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

const fetchDependencyOptions = async (currentSlug) => {
  try {
    const result = await skillApi.getSkillDependencyOptions(currentSlug)
    const data = result?.data || {}
    dependencyOptions.tools = data.tools || []
    dependencyOptions.mcps = data.mcps || []
    dependencyOptions.skills = data.skills || []
  } catch {
    // ignore
  }
}

const syncDependencyFormFromSkill = (skillRecord) => {
  dependencyForm.tool_dependencies = [...(skillRecord?.tool_dependencies || [])]
  dependencyForm.mcp_dependencies = [...(skillRecord?.mcp_dependencies || [])]
  dependencyForm.skill_dependencies = [...(skillRecord?.skill_dependencies || [])]
}

const normalizeTree = (nodes) =>
  (nodes || []).map((node) => ({
    title: node.name,
    key: node.path,
    isLeaf: !node.is_dir,
    path: node.path,
    is_dir: node.is_dir,
    children: node.is_dir ? normalizeTree(node.children || []) : undefined
  }))

const resetFileState = () => {
  pendingFileLoad.value = false
  selectedPath.value = ''
  selectedIsDir.value = false
  selectedTreeKeys.value = []
  expandedKeys.value = []
  fileContent.value = ''
}

const reloadContent = async () => {
  const result = await skillApi.getSkillContent(currentSkill.value.slug)
  const data = result.data
  savedFiles.value = { ...data.files }
  draftFiles.value = { ...data.files }
  contentRevision.value = data.revision
  treeData.value = normalizeTree(data.tree)
  nodeChanges.value = []
  syncDependencyFormFromSkill(readDraftDependencies(data.files['SKILL.md']))
}

const reloadTree = async () => {
  if (savingAny.value || pendingFileLoad.value) return
  if (!(await confirmDiscardFileDraft()) || savingAny.value || pendingFileLoad.value) return
  pendingFileLoad.value = true
  try {
    await reloadContent()
    await loadSkillFile(currentSkill.value.slug)
  } finally {
    pendingFileLoad.value = false
  }
}

const loadSkillFile = async (_skillSlug, path = 'SKILL.md') => {
  if (!(path in draftFiles.value)) {
    message.warning('此文件不能在线编辑，请导出查看')
    return false
  }
  fileContent.value = savedFiles.value[path] ?? draftFiles.value[path]
  selectedPath.value = path
  selectedIsDir.value = false
  selectedTreeKeys.value = [path]
  return true
}

const updateDraftFile = (content) => {
  if (savingAny.value || pendingFileLoad.value) return
  draftFiles.value[selectedPath.value] = content
  if (selectedPath.value === 'SKILL.md') {
    try {
      syncDependencyFormFromSkill(readDraftDependencies(content))
    } catch {
      /* 保留无效草稿，保存时显示校验结果。 */
    }
  }
}

const syncDependencyDraft = () => {
  try {
    draftFiles.value['SKILL.md'] = writeDraftDependencies(
      draftFiles.value['SKILL.md'],
      dependencyForm
    )
    if (selectedPath.value === 'SKILL.md') fileContent.value = savedFiles.value['SKILL.md']
  } catch (error) {
    message.error(error.message)
    syncDependencyFormFromSkill(currentSkill.value)
  }
}

const handleTreeSelect = async (keys, info) => {
  const previousPath = selectedPath.value
  const previousIsDir = selectedIsDir.value
  if (savingAny.value || pendingFileLoad.value) {
    selectedTreeKeys.value = previousPath ? [previousPath] : []
    message.warning('请等待当前操作完成')
    return
  }
  if (!keys?.length) {
    resetFileState()
    return
  }
  const node = info?.node || {}
  const path = node.path || node.key
  const isDir = !!node.is_dir
  selectedTreeKeys.value = [path]
  selectedPath.value = path
  selectedIsDir.value = isDir
  if (isDir) {
    pendingFileLoad.value = false
    fileContent.value = ''
    return
  }
  if (!(await loadSkillFile(currentSkill.value.slug, path)) && selectedPath.value === path) {
    selectedPath.value = previousPath
    selectedIsDir.value = previousIsDir
    selectedTreeKeys.value = previousPath ? [previousPath] : []
  }
}

const saveCurrentFile = async (content) => {
  updateDraftFile(content)
  await saveContent()
}

const saveContent = async () => {
  if (savingAny.value || pendingFileLoad.value || !canEditSkill.value) return
  savingFile.value = true
  try {
    const result = await skillApi.saveSkillContent(currentSkill.value.slug, {
      expected_revision: contentRevision.value,
      changes: collectSkillChanges(savedFiles.value, draftFiles.value, nodeChanges.value),
      release: false
    })
    currentSkill.value = result.data.skill
    savedFiles.value = { ...draftFiles.value }
    contentRevision.value = result.data.revision
    nodeChanges.value = []
    fileContent.value = draftFiles.value[selectedPath.value] || ''
    syncDependencyFormFromSkill(result.data.skill)
    message.success('已保存全部修改')
  } catch (error) {
    message.error(resolveApiErrorMessage(error, '保存失败，全部修改仍保留'))
  } finally {
    savingFile.value = false
  }
}

const confirmDeleteSkill = () => {
  const target = currentSkill.value
  if (!target || !canManageCurrentSkill.value || isBuiltinInstalledSkill.value) return
  const actionText = '删除'
  Modal.confirm({
    title: `确认${actionText}技能「${target.slug}」？`,
    content: '删除后无法恢复，所有文件和配置将永久消失。',
    okText: `确认${actionText}`,
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await skillApi.deleteSkill(target.slug)
        skillDeleted.value = true
        message.success(`已${actionText}`)
        if (route.query.agent)
          router.push({ name: 'AgentManageComp', query: { agent: route.query.agent } })
        else router.push({ path: '/extensions', query: { tab: 'skills' } })
      } catch {
        message.error(`${actionText}失败`)
      }
    }
  })
}

const handleExport = async () => {
  if (!currentSkill.value || !isInstalledSkill.value || !canManageCurrentSkill.value) return
  try {
    const response = await skillApi.exportSkill(currentSkill.value.slug)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${currentSkill.value.slug}.zip`
    link.click()
    URL.revokeObjectURL(url)
  } catch {
    message.error('导出失败')
  }
}

const openCreateModal = (isDir) => {
  if (savingAny.value || pendingFileLoad.value || !currentSkill.value || !canEditSkill.value) return
  createForm.path = ''
  createForm.content = ''
  createForm.isDir = isDir
  createModalVisible.value = true
}

const handleCreateNode = async () => {
  const path = createForm.path.trim()
  if (
    savingAny.value ||
    pendingFileLoad.value ||
    !currentSkill.value ||
    !path ||
    !canEditSkill.value
  )
    return
  if (
    path.startsWith('/') ||
    path.split('/').some((part) => !part || part === '.' || part === '..') ||
    path.includes('\\')
  ) {
    message.error('请输入 Skill 内的相对路径')
    return
  }
  const segments = path.split('/')
  let existing = treeData.value
  for (let index = 0; index < segments.length; index += 1) {
    const key = segments.slice(0, index + 1).join('/')
    const node = existing.find((item) => item.key === key)
    if (!node) break
    if (index === segments.length - 1 || !node.is_dir) {
      message.error(index === segments.length - 1 ? '路径已存在' : '父路径是文件，不能新建子节点')
      return
    }
    existing = node.children || []
  }
  nodeChanges.value.push({
    action: createForm.isDir ? 'mkdir' : 'create',
    path,
    content: createForm.content
  })
  if (!createForm.isDir) draftFiles.value[path] = createForm.content
  let nodes = treeData.value
  for (let index = 0; index < segments.length; index += 1) {
    const key = segments.slice(0, index + 1).join('/')
    let node = nodes.find((item) => item.key === key)
    if (!node) {
      const isDir = index < segments.length - 1 || createForm.isDir
      node = {
        key,
        path: key,
        title: segments[index],
        is_dir: isDir,
        isLeaf: !isDir,
        ...(isDir ? { children: [] } : {})
      }
      nodes.push(node)
    }
    nodes = node.children
  }
  createModalVisible.value = false
  message.success('已加入草稿，保存后生效')
}

const saveShareConfig = async () => {
  if (
    savingAny.value ||
    !currentSkill.value ||
    !isInstalledSkill.value ||
    !canManageCurrentSkill.value
  )
    return
  if (!isBuiltinInstalledSkill.value) {
    const validation = shareConfigFormRef.value?.validate?.()
    if (validation && !validation.valid) {
      message.warning(validation.message || '请完善 Skill 生效范围')
      return
    }
  }

  savingShareConfig.value = true
  try {
    if (!isBuiltinInstalledSkill.value) {
      await skillApi.updateSkillShareConfig(currentSkill.value.slug, shareConfigForm.value)
    }
    const result = await skillApi.updateSkillEnabled(currentSkill.value.slug, enabledForm.value)
    if (result?.data) {
      currentSkill.value = result.data
      syncShareConfigFromSkill(result.data)
    }
    message.success('设置已保存')
  } catch (error) {
    message.error(resolveApiErrorMessage(error, '保存设置失败'))
  } finally {
    savingShareConfig.value = false
  }
}

onMounted(() => {
  window.addEventListener('beforeunload', warnBeforeUnload)
  fetchSkillDetail()
})

onUnmounted(() => window.removeEventListener('beforeunload', warnBeforeUnload))

onBeforeRouteLeave(async () => {
  if (skillDeleted.value) return true
  if (savingAny.value) {
    message.warning('请等待当前操作完成')
    return false
  }
  return confirmDiscardFileDraft(true)
})
</script>

<style lang="less" scoped>
.skill-editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--gray-150);
  min-height: 49px;
  flex-shrink: 0;
}
.skill-editor-path {
  color: var(--gray-600);
  font-family: monospace;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-detail {
  .readonly-detail-hint {
    width: min(100%, 860px);
    margin: 16px auto 0;
  }

  .tree-toggle {
    &.active {
      background: var(--main-10);
      color: var(--main-color);
    }
  }
}

.editor-tab-content {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.workspace {
  width: min(calc(100% - 48px), 768px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 0;
  gap: 0;
  flex: 1;
  min-height: 0;
  height: 100%;
  margin: 0 auto;
  padding: 20px 0 24px;
  overflow: hidden;
  transition:
    width 220ms ease,
    grid-template-columns 220ms ease,
    gap 220ms ease;

  &.tree-visible {
    width: min(calc(100% - 48px), 1100px);
    grid-template-columns: minmax(0, 1fr) 252px;
    gap: 20px;
  }
}

.tree-container {
  grid-column: 2;
  grid-row: 1;
  min-width: 0;
  min-height: 0;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  display: flex;
  flex-direction: column;
  overflow: hidden;

  .tree-header {
    min-height: 44px;
    padding: 8px 10px 8px 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--gray-100);

    .label {
      font-size: 13px;
      font-weight: 600;
      color: var(--gray-700);
    }

    .tree-actions {
      display: flex;
      gap: 2px;

      button {
        width: 28px;
        height: 28px;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        border: 0;
        border-radius: 6px;
        background: transparent;
        color: var(--gray-500);
        cursor: pointer;

        &:hover:not(:disabled),
        &:focus-visible:not(:disabled) {
          color: var(--gray-900);
          background: var(--gray-50);
          outline: none;
        }

        &:disabled {
          color: var(--gray-300);
          cursor: not-allowed;
        }
      }
    }
  }

  .tree-content {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 8px 10px 12px;
  }
}

.skill-tree-enter-active,
.skill-tree-leave-active {
  transition:
    opacity 160ms ease,
    transform 220ms ease;
}

.skill-tree-enter-from,
.skill-tree-leave-to {
  opacity: 0;
  transform: translateX(12px);
}

.editor-container {
  grid-column: 1;
  grid-row: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;

  .editor-main {
    flex: 1;
    min-height: 0;
    background: transparent;
    display: flex;
    flex-direction: column;
  }

  .editor-main :deep(.ant-empty) {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .skill-file-preview {
    flex: 1;
    min-height: 0;
    border-radius: 0;
  }

  :deep(.skill-file-preview-content) {
    flex: 1;
    min-height: 0;
    max-height: none;
  }

  :deep(.skill-file-preview.is-full-height .file-content) {
    scrollbar-width: none;
    -ms-overflow-style: none;
  }

  :deep(.skill-file-preview.is-full-height .file-content::-webkit-scrollbar) {
    display: none;
  }

  :deep(.skill-file-preview-content .file-content-pre.code-highlight code) {
    min-height: 100%;
  }

  :deep(.skill-file-preview .frontmatter-card .fm-row) {
    grid-template-columns: minmax(150px, 180px) minmax(0, 1fr);
  }
}

.settings-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 18px 0;

  &.scope-card {
    display: block;
  }
}

.config-view .config-section-body {
  padding: 0 20px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: transparent;
  box-shadow: none;
}

.settings-stack > .settings-card:last-child,
.dependency-groups > .dependency-card:last-child {
  border-bottom: 0;
}

.settings-card-main {
  min-width: 0;
}

.settings-card-title {
  margin-bottom: 4px;
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 700;
}

.settings-card-desc {
  color: var(--gray-500);
  font-size: 13px;
  line-height: 1.55;
}

.settings-card-action {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 10px;
}

.scope-card .settings-card-main {
  margin-bottom: 14px;
}

.status-pill {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 18px;

  &.enabled {
    background: var(--main-10);
    color: var(--main-color);
  }

  &.disabled {
    background: var(--gray-100);
    color: var(--gray-500);
  }
}

.readonly-scope-hint {
  color: var(--gray-500);
  background: var(--gray-50);
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  padding: 11px 12px;
  font-size: 13px;
  line-height: 1.55;
}

.dependency-card {
  padding: 18px 0;

  &.readonly {
    background: transparent;
  }
}

.dependency-card-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.dependency-title-block {
  min-width: 0;
  flex: 1;
}

.dependency-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;

  h4 {
    margin: 0;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 700;
  }
}

.dependency-title-block p {
  margin: 4px 0 0;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.45;
}

.dependency-count {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--gray-50);
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.dependency-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  flex-shrink: 0;
  gap: 5px;
  padding: 0 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.dependency-select-btn {
  border-color: var(--gray-100);
  background: var(--gray-50);
  box-shadow: none;

  &:hover,
  &:focus {
    border-color: var(--main-color);
    background: var(--main-20);
    color: var(--main-color);
  }
}

.dependency-select-chevron {
  opacity: 0.72;
}

.dependency-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.dependency-chip {
  display: inline-flex;
  align-items: center;
  max-width: 220px;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-50);
  color: var(--gray-700);
  font-size: 12px;
  line-height: 18px;

  span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.dependency-chip-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;

  &:hover {
    background: var(--gray-150);
    color: var(--gray-800);
  }
}

.dependency-empty-hint {
  margin-top: 14px;
  padding: 10px 12px;
  border: 1px dashed var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
  color: var(--gray-500);
  font-size: 12px;
}

@media (max-width: 900px) {
  .workspace {
    display: flex;
    flex-direction: column;
    overflow-y: auto;
  }

  .tree-container {
    order: 0;
    width: 100%;
    height: 220px;
    flex: 0 0 auto;
  }

  .editor-container {
    order: 1;
    min-height: 520px;
    flex: 0 0 auto;
  }
}

@media (prefers-reduced-motion: reduce) {
  .workspace,
  .skill-tree-enter-active,
  .skill-tree-leave-active {
    transition: none;
  }
}

@media (max-width: 768px) {
  :deep(.extension-detail-tabs > .ant-tabs-nav) {
    flex-wrap: wrap;
    gap: 4px;
    padding: 8px 12px;
  }

  :deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-extra-content:first-child) {
    flex: 0 0 auto;
  }

  :deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-nav-wrap) {
    order: 2;
    flex: 1 1 100%;
  }

  :deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-extra-content:last-child) {
    flex: 1 1 auto;
  }

  .config-view .config-section-body {
    padding: 0 16px;
  }

  .editor-main :deep(.skill-file-preview .frontmatter-card .fm-row) {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }

  .editor-main :deep(.skill-file-preview .frontmatter-card .fm-value) {
    overflow-wrap: anywhere;
  }

  .workspace,
  .workspace.tree-visible {
    width: min(calc(100% - 32px), 768px);
  }

  .settings-card {
    flex-direction: column;
    align-items: stretch;
  }

  .dependency-chip-list,
  .dependency-empty-hint {
    margin-left: 0;
    padding-left: 0;
  }
}

.mt-40 {
  margin-top: 40px;
}
.pt-12 {
  padding-top: 12px;
}
</style>

<style lang="less">
.dependency-selection-popover {
  .selection-dropdown {
    width: 300px;
    max-height: 360px;
    padding: 8px;
    overflow: hidden auto;
    border: 1px solid var(--gray-200);
    border-radius: 14px;
    background: var(--gray-0);
    box-shadow: var(--shadow-popover);
  }

  .selection-dropdown-header {
    padding: 8px 10px 10px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--gray-100);
  }

  .selection-dropdown-title {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 700;
    line-height: 1.4;
  }

  .selection-dropdown-subtitle {
    margin-top: 2px;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.4;
  }

  .selection-search {
    width: calc(100% - 16px);
    height: 30px;
    margin: 8px;
  }

  .selection-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .selection-item {
    display: flex;
    align-items: center;
    min-height: 38px;
    gap: 8px;
    padding: 8px 10px;
    border-radius: 9px;
    color: var(--gray-800);
    cursor: pointer;
    transition:
      background-color 160ms ease,
      color 160ms ease;

    &:hover {
      background: var(--gray-50);
    }

    &.selected {
      background: var(--main-10);
      color: var(--gray-900);
    }
  }

  .selection-item-content {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 8px;
  }

  .selection-label {
    min-width: 0;
    overflow: hidden;
    font-size: 13px;
    line-height: 18px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .selection-empty {
    display: block;
    padding: 16px 0;
    color: var(--gray-600);
    font-size: 13px;
    text-align: center;
  }
}
@media (max-width: 768px) {
  :deep(.extension-detail-tabs > .ant-tabs-nav) {
    flex-wrap: wrap;
    padding-top: 10px;
    padding-bottom: 10px;
  }

  :deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-extra-content) {
    flex: 1 1 100%;
  }

  .extension-detail-breadcrumb {
    width: 100%;
    min-width: 0;
  }

  .extension-detail-current {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .detail-actions :deep(.ant-space) {
    flex-wrap: wrap;
  }
}
</style>
