<template>
  <div class="skill-detail extension-detail-page">
    <div v-if="loading" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>
    <div class="detail-top-bar">
      <button class="detail-back-btn" @click="goBack">
        <ArrowLeft :size="16" />
        <span>返回</span>
      </button>
      <div class="detail-title-area">
        <div class="detail-icon">
          <BookMarked :size="18" />
        </div>
        <div class="detail-title-text">
          <h2>{{ currentSkill?.name || slug }}</h2>
          <span class="detail-subtitle">{{ currentSkillStatusLabel }}</span>
        </div>
      </div>
      <div class="detail-actions">
        <a-space :size="8">
          <button
            v-if="currentSkill?.is_builtin_spec && currentSkill?.status === 'not_installed'"
            type="button"
            @click="handleInstallBuiltin(currentSkill)"
            class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
          >
            <span>安装</span>
          </button>
          <button
            v-if="currentSkill?.is_builtin_spec && currentSkill?.status === 'update_available'"
            type="button"
            @click="handleUpdateBuiltin(currentSkill)"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
          >
            <span>更新</span>
          </button>
          <button
            v-if="isInstalledSkill"
            type="button"
            @click="handleExport"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
          >
            <Download :size="14" />
            <span>导出</span>
          </button>
          <button
            v-if="isInstalledSkill"
            type="button"
            @click="confirmDeleteSkill"
            class="lucide-icon-btn extension-panel-action extension-panel-action-danger"
          >
            <Trash2 :size="14" />
            <span>{{ isBuiltinInstalledSkill ? '卸载' : '删除' }}</span>
          </button>
        </a-space>
      </div>
    </div>

    <div class="detail-content-wrapper">
      <div v-if="currentSkill" class="detail-content-inner">
        <div v-if="!isInstalledSkill" class="builtin-uninstalled-state">
          <h3>{{ currentSkill.description }}</h3>
          <p>版本 {{ currentSkill.version }}</p>
          <a-button type="primary" @click="handleInstallBuiltin(currentSkill)"
            >安装内置 Skill</a-button
          >
        </div>

        <a-tabs v-else v-model:activeKey="activeTab" class="minimal-tabs">
          <a-tab-pane key="editor">
            <template #tab>
              <span class="tab-title"><FileText :size="14" />代码管理</span>
            </template>
            <div class="workspace">
              <div class="tree-container">
                <div class="tree-header">
                  <span class="label">项目结构</span>
                  <div class="tree-actions">
                    <a-tooltip v-if="!isBuiltinInstalledSkill" title="新建文件"
                      ><button @click="openCreateModal(false)"><FilePlus :size="14" /></button
                    ></a-tooltip>
                    <a-tooltip v-if="!isBuiltinInstalledSkill" title="新建目录"
                      ><button @click="openCreateModal(true)"><FolderPlus :size="14" /></button
                    ></a-tooltip>
                    <a-tooltip title="刷新"
                      ><button @click="reloadTree"><RotateCw :size="14" /></button
                    ></a-tooltip>
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
              <div class="editor-container">
                <div class="editor-main">
                  <a-empty
                    v-if="!selectedPath || selectedIsDir"
                    description="选择文件以开始编辑"
                    class="mt-40"
                  />
                  <template v-else>
                    <AgentFilePreview
                      :file="selectedFilePreview"
                      :file-path="selectedPath"
                      :show-download="false"
                      :show-fullscreen="true"
                      :editable="!isBuiltinInstalledSkill"
                      :edit-all-text="true"
                      :saving="savingFile"
                      :full-height="true"
                      container-class="skill-file-preview"
                      content-class="skill-file-preview-content"
                      @save="saveCurrentFile"
                    />
                  </template>
                </div>
              </div>
            </div>
          </a-tab-pane>

          <a-tab-pane key="dependencies">
            <template #tab>
              <span class="tab-title"><Layers :size="14" />依赖管理</span>
            </template>
            <div class="config-view">
              <div class="config-header">
                <div class="text">
                  <h3>依赖声明</h3>
                  <p>配置此 Skill 所需的工具、MCP 及其他 Skill 依赖。</p>
                </div>
                <a-button
                  type="primary"
                  :loading="savingDependencies"
                  @click="saveDependencies"
                  class="lucide-icon-btn"
                >
                  <Save :size="14" />
                  <span>更新依赖</span>
                </a-button>
              </div>
              <div class="config-form">
                <a-form layout="vertical">
                  <a-form-item label="工具依赖 (Tools)">
                    <a-select
                      v-model:value="dependencyForm.tool_dependencies"
                      mode="multiple"
                      :options="toolDependencyOptions"
                      placeholder="选择工具..."
                      allow-clear
                      show-search
                    />
                  </a-form-item>
                  <a-form-item label="MCP 依赖 (Model Context Protocol)">
                    <a-select
                      v-model:value="dependencyForm.mcp_dependencies"
                      mode="multiple"
                      :options="mcpDependencyOptions"
                      placeholder="选择 MCP 服务..."
                      allow-clear
                      show-search
                    />
                  </a-form-item>
                  <a-form-item label="Skill 依赖">
                    <a-select
                      v-model:value="dependencyForm.skill_dependencies"
                      mode="multiple"
                      :options="skillDependencyOptions"
                      placeholder="选择 Skill..."
                      allow-clear
                      show-search
                    />
                  </a-form-item>
                </a-form>
              </div>
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>
      <div v-else-if="!loading" class="detail-empty">
        <a-empty description="未找到 Skill" />
      </div>
    </div>

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
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  ArrowLeft,
  BookMarked,
  Download,
  Trash2,
  Save,
  FileText,
  Layers,
  FilePlus,
  FolderPlus,
  RotateCw
} from 'lucide-vue-next'
import { skillApi } from '@/apis/skill_api'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import FileTreeComponent from '@/components/FileTreeComponent.vue'

const route = useRoute()
const router = useRouter()
const slug = computed(() => decodeURIComponent(route.params.slug))

const loading = ref(false)
const currentSkill = ref(null)
const treeData = ref([])
const selectedTreeKeys = ref([])
const expandedKeys = ref([])
const selectedPath = ref('')
const selectedIsDir = ref(false)
const fileContent = ref('')
const savingFile = ref(false)
const creatingNode = ref(false)
const savingDependencies = ref(false)
const activeTab = ref('editor')

const skills = ref([])
const builtinSkills = ref([])
const createModalVisible = ref(false)
const createForm = reactive({ path: '', isDir: false, content: '' })
const dependencyOptions = reactive({ tools: [], mcps: [], skills: [] })
const dependencyForm = reactive({
  tool_dependencies: [],
  mcp_dependencies: [],
  skill_dependencies: []
})

const isInstalledSkill = computed(() => {
  return !!(
    currentSkill.value &&
    (currentSkill.value.installed_record || currentSkill.value.dir_path)
  )
})

const isBuiltinInstalledSkill = computed(() => {
  return !!(
    isInstalledSkill.value &&
    (currentSkill.value?.is_builtin || currentSkill.value?.installed_record)
  )
})

const currentSkillStatusLabel = computed(() => {
  const skill = currentSkill.value
  if (!skill) return ''
  if (skill.is_builtin_spec) {
    if (skill.status === 'not_installed') return '未安装'
    if (skill.status === 'update_available') return '更新可用'
    return '已安装'
  }
  if (skill.is_builtin) return '已安装'
  return '已上传'
})

const selectedFilePreview = computed(() => ({
  content: fileContent.value,
  previewType: 'text',
  supported: true
}))

const toolDependencyOptions = computed(() =>
  (dependencyOptions.tools || []).map((i) =>
    typeof i === 'object' ? { label: i.name, value: i.id } : { label: i, value: i }
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

const goBack = () => {
  router.push({ path: '/extensions', query: { tab: 'skills' } })
}

const fetchSkillDetail = async () => {
  loading.value = true
  try {
    const [skillResult, builtinResult] = await Promise.all([
      skillApi.listSkills(),
      skillApi.listBuiltinSkills()
    ])
    skills.value = skillResult?.data || []
    builtinSkills.value = (builtinResult?.data || []).map((item) => ({
      ...item,
      ...(item.installed_record || {}),
      is_builtin_spec: true
    }))

    const allSkills = [
      ...builtinSkills.value,
      ...skills.value.filter((s) => !builtinSkills.value.find((b) => b.slug === s.slug))
    ]
    const found = allSkills.find((s) => s.slug === slug.value)
    if (found) {
      currentSkill.value = found
      syncDependencyFormFromSkill(found.installed_record || found)
      if (found.installed_record || found.dir_path) {
        await reloadTree()
        await loadSkillFile(found.slug)
      }
    }
    await fetchDependencyOptions()
  } catch {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

const fetchDependencyOptions = async () => {
  try {
    const result = await skillApi.getSkillDependencyOptions()
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
  selectedPath.value = ''
  selectedIsDir.value = false
  selectedTreeKeys.value = []
  expandedKeys.value = []
  fileContent.value = ''
}

const expandAllKeys = (nodes) =>
  nodes.flatMap((node) => (node.is_dir ? [node.key, ...expandAllKeys(node.children || [])] : []))

const reloadTree = async () => {
  if (!currentSkill.value || !isInstalledSkill.value) return
  loading.value = true
  try {
    const result = await skillApi.getSkillTree(currentSkill.value.slug)
    const normalized = normalizeTree(result?.data || [])
    treeData.value = normalized
    expandedKeys.value = expandAllKeys(normalized)
  } catch {
    message.error('加载目录树失败')
  } finally {
    loading.value = false
  }
}

const loadSkillFile = async (skillSlug, path = 'SKILL.md') => {
  try {
    const fileResult = await skillApi.getSkillFile(skillSlug, path)
    const content = fileResult?.data?.content || ''
    fileContent.value = content
    selectedPath.value = path
    selectedIsDir.value = false
    selectedTreeKeys.value = [path]
  } catch {
    // file not found is ok
  }
}

const handleTreeSelect = async (keys, info) => {
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
    fileContent.value = ''
    return
  }
  try {
    const result = await skillApi.getSkillFile(currentSkill.value.slug, path)
    const content = result?.data?.content || ''
    fileContent.value = content
  } catch {
    message.error('文件读取失败')
  }
}

const saveCurrentFile = async (content = fileContent.value) => {
  if (
    !currentSkill.value ||
    !selectedPath.value ||
    selectedIsDir.value ||
    isBuiltinInstalledSkill.value
  )
    return
  savingFile.value = true
  try {
    await skillApi.updateSkillFile(currentSkill.value.slug, {
      path: selectedPath.value,
      content
    })
    fileContent.value = content
    message.success('已保存')
    if (selectedPath.value === 'SKILL.md') await fetchSkillDetail()
  } catch {
    message.error('保存失败')
  } finally {
    savingFile.value = false
  }
}

const handleInstallBuiltin = async (record) => {
  if (!record?.slug) return
  loading.value = true
  try {
    await skillApi.installBuiltinSkill(record.slug)
    await fetchSkillDetail()
    message.success('安装成功')
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '安装失败')
  } finally {
    loading.value = false
  }
}

const handleUpdateBuiltin = async (record) => {
  if (!record?.slug) return
  loading.value = true
  try {
    await skillApi.updateBuiltinSkill(record.slug, false)
    await fetchSkillDetail()
    message.success('更新成功')
  } catch (error) {
    if (error.response?.data?.detail?.needs_confirm) {
      loading.value = false
      Modal.confirm({
        title: '确认覆盖更新？',
        content: '检测到你修改过此 skill，更新将覆盖你的修改，是否继续？',
        okText: '继续更新',
        cancelText: '取消',
        onOk: async () => {
          loading.value = true
          try {
            await skillApi.updateBuiltinSkill(record.slug, true)
            await fetchSkillDetail()
            message.success('更新成功')
          } catch (forceError) {
            message.error(forceError?.response?.data?.detail || forceError.message || '更新失败')
          } finally {
            loading.value = false
          }
        }
      })
      return
    }
    message.error(error?.response?.data?.detail || error.message || '更新失败')
  } finally {
    loading.value = false
  }
}

const confirmDeleteSkill = () => {
  const target = currentSkill.value
  if (!target) return
  const isBuiltinTarget = !!(
    target?.is_builtin ||
    target?.installed_record ||
    target?.sourceType === 'builtin'
  )
  const actionText = isBuiltinTarget ? '卸载' : '删除'
  Modal.confirm({
    title: `确认${actionText}技能「${target.slug}」？`,
    content: isBuiltinTarget
      ? '卸载后会移除已安装文件和数据库记录，但仍可从"未安装 Skills"中重新安装。'
      : '删除后无法恢复，所有文件和配置将永久消失。',
    okText: `确认${actionText}`,
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await skillApi.deleteSkill(target.slug)
        message.success(`已${actionText}`)
        router.push({ path: '/extensions', query: { tab: 'skills' } })
      } catch {
        message.error(`${actionText}失败`)
      }
    }
  })
}

const handleExport = async () => {
  if (!currentSkill.value || !isInstalledSkill.value) return
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
  if (!currentSkill.value) return
  createForm.path = ''
  createForm.content = ''
  createForm.isDir = isDir
  createModalVisible.value = true
}

const handleCreateNode = async () => {
  if (!currentSkill.value || !createForm.path.trim() || isBuiltinInstalledSkill.value) return
  creatingNode.value = true
  try {
    await skillApi.createSkillFile(currentSkill.value.slug, {
      path: createForm.path.trim(),
      is_dir: createForm.isDir,
      content: createForm.content
    })
    createModalVisible.value = false
    await reloadTree()
    message.success('创建成功')
  } catch {
    message.error('创建失败')
  } finally {
    creatingNode.value = false
  }
}

const saveDependencies = async () => {
  if (!currentSkill.value || !isInstalledSkill.value) return
  savingDependencies.value = true
  try {
    const result = await skillApi.updateSkillDependencies(currentSkill.value.slug, {
      tool_dependencies: dependencyForm.tool_dependencies,
      mcp_dependencies: dependencyForm.mcp_dependencies,
      skill_dependencies: dependencyForm.skill_dependencies
    })
    const updated = result?.data
    if (updated) {
      currentSkill.value = updated
      syncDependencyFormFromSkill(updated)
    }
    await fetchSkillDetail()
    message.success('依赖已更新')
  } catch {
    message.error('更新失败')
  } finally {
    savingDependencies.value = false
  }
}

onMounted(() => {
  fetchSkillDetail()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
@import '@/assets/css/extension-detail.less';

.skill-detail {
  .detail-content-wrapper {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    background-color: var(--gray-10);
  }

  .detail-content-inner {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  :deep(.minimal-tabs) {
    height: 100%;
  }
}

.builtin-uninstalled-state {
  padding: 24px;
  h3 {
    margin: 0 0 8px;
    font-size: 16px;
  }
  p {
    margin: 0 0 16px;
    color: var(--gray-500);
  }
}

.workspace {
  display: flex;
  flex: 1;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.tree-container {
  width: 240px;
  border-right: 1px solid var(--gray-150);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;

  .tree-header {
    padding: 10px var(--page-padding) 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    .label {
      font-size: 12px;
      font-weight: 600;
      color: var(--gray-500);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .tree-actions {
      display: flex;
      gap: 4px;
      button {
        background: none;
        border: none;
        padding: 2px;
        cursor: pointer;
        color: var(--gray-500);
        display: flex;
        align-items: center;
        &:hover {
          color: var(--gray-900);
        }
      }
    }
  }

  .tree-content {
    flex: 1;
    overflow-y: auto;
    height: 100%;
    padding: 8px calc(var(--page-padding) - 4px);
  }
}

.editor-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;


  .editor-main {
    flex: 1;
    min-height: 0;
    background-color: var(--gray-0);
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

  :deep(.skill-file-preview-content .file-edit-textarea) {
    min-height: 100%;
  }

  :deep(.skill-file-preview-content .file-content-pre.code-highlight code) {
    min-height: 100%;
  }
}

.config-view {
  padding: 16px;
  flex: 1;
  overflow-y: auto;
  max-width: 720px;
  .config-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 32px;
    flex-shrink: 0;
    .text {
      h3 {
        margin: 0 0 4px 0;
        font-size: 16px;
        font-weight: 600;
      }
      p {
        margin: 0;
        color: var(--gray-500);
        font-size: 13px;
      }
    }
  }
  .config-form {
    max-width: 600px;
    :deep(.ant-form-item-label label) {
      font-weight: 500;
      font-size: 13px;
    }
  }
}

.mt-40 {
  margin-top: 40px;
}
.pt-12 {
  padding-top: 12px;
}
</style>
