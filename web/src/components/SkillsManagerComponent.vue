<template>
  <div class="skills-manager-container extension-page-root">
    <div v-if="loading" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>
    <div class="layout-wrapper" :class="{ 'content-loading': loading }">
      <!-- 左侧：技能列表 -->
      <div class="sidebar-list">
        <div class="search-box">
          <a-input
            v-model:value="searchQuery"
            placeholder="搜索技能..."
            allow-clear
            class="search-input"
          >
            <template #prefix><Search :size="14" class="text-muted" /></template>
          </a-input>
        </div>

        <div class="list-container">
          <div v-if="filteredSkills.length === 0" class="empty-text">
            <a-empty :image="false" description="无匹配技能" />
          </div>
          <template v-for="(skill, index) in filteredSkills" :key="skill.slug">
            <div
              class="list-item"
              :class="{ active: currentSkill?.slug === skill.slug }"
              @click="selectSkill(skill)"
            >
              <div class="item-header">
                <BookMarked :size="16" class="item-icon" />
                <span class="item-name">{{ skill.name }}</span>
              </div>
              <div class="item-details item-details-inline">
                <span class="item-slug item-meta mono-text">{{ skill.slug }}</span>
                <div class="item-badges">
                  <span
                    v-if="skill.tool_dependencies?.length"
                    class="dot-badge blue"
                    title="工具依赖"
                  ></span>
                  <span
                    v-if="skill.mcp_dependencies?.length"
                    class="dot-badge green"
                    title="MCP依赖"
                  ></span>
                </div>
              </div>
            </div>
            <div v-if="index < filteredSkills.length - 1" class="list-separator"></div>
          </template>
        </div>
      </div>

      <!-- 右侧：详情面板 -->
      <div class="main-panel">
        <div v-if="!currentSkill" class="unselected-state">
          <div class="hint-box">
            <FileCode :size="40" class="text-muted" />
            <p>请在左侧选择技能包进行编辑</p>
          </div>
        </div>

        <template v-else>
          <div class="panel-top-bar">
            <div class="panel-title-stack">
              <h2>{{ currentSkill.name }}</h2>
              <!-- <code>{{ currentSkill.slug }}</code> -->
            </div>
            <div class="panel-actions">
              <a-space :size="8">
                <a-button size="small" @click="handleExport" class="lucide-icon-btn">
                  <Download :size="14" />
                  <span>导出</span>
                </a-button>
                <a-button
                  size="small"
                  danger
                  ghost
                  @click="confirmDeleteSkill"
                  class="lucide-icon-btn"
                >
                  <Trash2 :size="14" />
                  <span>删除</span>
                </a-button>
              </a-space>
            </div>
          </div>

          <a-tabs v-model:activeKey="activeTab" class="minimal-tabs">
            <a-tab-pane key="editor">
              <template #tab>
                <span class="tab-title"><FileText :size="14" />代码管理</span>
              </template>
              <div class="workspace">
                <div class="tree-container">
                  <div class="tree-header">
                    <span class="label">项目结构</span>
                    <div class="tree-actions">
                      <a-tooltip title="新建文件"
                        ><button @click="openCreateModal(false)"><FilePlus :size="14" /></button
                      ></a-tooltip>
                      <a-tooltip title="新建目录"
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
                  <div class="editor-header">
                    <div class="current-path">
                      <File :size="14" />
                      <span>{{ selectedPath || '未选择文件' }}</span>
                      <span v-if="canSave" class="save-hint">●</span>
                    </div>
                    <div class="header-actions">
                      <a-button
                        v-if="isMarkdownFile && selectedPath"
                        size="small"
                        @click="viewMode = viewMode === 'edit' ? 'preview' : 'edit'"
                        class="lucide-icon-btn view-toggle-btn"
                        :title="viewMode === 'edit' ? '预览' : '编辑'"
                      >
                        <Eye v-if="viewMode === 'edit'" :size="14" />
                        <Edit3 v-else :size="14" />
                        <span>{{ viewMode === 'edit' ? '预览' : '编辑' }}</span>
                      </a-button>
                      <a-button
                        type="primary"
                        size="small"
                        @click="saveCurrentFile"
                        :disabled="!canSave"
                        :loading="savingFile"
                        class="lucide-icon-btn"
                      >
                        <Save :size="14" />
                        <span>保存</span>
                      </a-button>
                    </div>
                  </div>
                  <div class="editor-main">
                    <a-empty
                      v-if="!selectedPath || selectedIsDir"
                      description="选择文件以开始编辑"
                      class="mt-40"
                    />
                    <template v-else>
                      <MdPreview
                        v-if="viewMode === 'preview'"
                        :modelValue="fileContent"
                        :theme="theme"
                        previewTheme="github"
                        class="markdown-preview flat-md-preview"
                      />
                      <a-textarea
                        v-else
                        v-model:value="fileContent"
                        class="pure-editor"
                        spellcheck="false"
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
                    <p>配置此 Skill 所需的工具、MCP 服务器及其他 Skill 依赖。</p>
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
        </template>
      </div>
    </div>

    <!-- 弹窗 -->
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import { useThemeStore } from '@/stores/theme'
import {
  Upload,
  RotateCw,
  Download,
  Trash2,
  Save,
  FileText,
  Layers,
  FilePlus,
  FolderPlus,
  File,
  Search,
  BookMarked,
  FileCode,
  Eye,
  Edit3
} from 'lucide-vue-next'
import { skillApi } from '@/apis/skill_api'
import FileTreeComponent from '@/components/FileTreeComponent.vue'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

const themeStore = useThemeStore()
const theme = computed(() => (themeStore.isDark ? 'dark' : 'light'))

const loading = ref(false)
const importing = ref(false)
const savingFile = ref(false)
const creatingNode = ref(false)
const savingDependencies = ref(false)
const activeTab = ref('editor')
const searchQuery = ref('')
const viewMode = ref('edit') // 'edit' | 'preview'

const skills = ref([])
const currentSkill = ref(null)
const treeData = ref([])
const selectedTreeKeys = ref([])
const expandedKeys = ref([])
const selectedPath = ref('')
const selectedIsDir = ref(false)
const fileContent = ref('')
const originalFileContent = ref('')

const createModalVisible = ref(false)
const createForm = reactive({ path: '', isDir: false, content: '' })
const dependencyOptions = reactive({ tools: [], mcps: [], skills: [] })
const dependencyForm = reactive({
  tool_dependencies: [],
  mcp_dependencies: [],
  skill_dependencies: []
})

const filteredSkills = computed(() => {
  if (!searchQuery.value) return skills.value
  const q = searchQuery.value.toLowerCase()
  return skills.value.filter(
    (s) => s.name.toLowerCase().includes(q) || s.slug.toLowerCase().includes(q)
  )
})

const canSave = computed(() => {
  if (!selectedPath.value || selectedIsDir.value) return false
  return fileContent.value !== originalFileContent.value
})

const isMarkdownFile = computed(() => {
  if (!selectedPath.value) return false
  return selectedPath.value.toLowerCase().endsWith('.md')
})

// 切换到非markdown文件时重置为编辑模式
watch(selectedPath, (newPath) => {
  if (newPath && !newPath.toLowerCase().endsWith('.md')) {
    viewMode.value = 'edit'
  }
})

const formatRelativeTime = (time) => (time ? dayjs(time).fromNow() : '-')

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
  originalFileContent.value = ''
  viewMode.value = 'edit'
}

const expandAllKeys = (nodes) =>
  nodes.flatMap((node) => (node.is_dir ? [node.key, ...expandAllKeys(node.children || [])] : []))

const fetchSkills = async () => {
  loading.value = true
  try {
    const result = await skillApi.listSkills()
    skills.value = result?.data || []

    // 默认选中第一个技能并加载 SKILL.md
    if (!currentSkill.value && skills.value.length > 0) {
      await selectSkill(skills.value[0])
    } else if (currentSkill.value) {
      const latest = skills.value.find((i) => i.slug === currentSkill.value.slug)
      if (latest) {
        currentSkill.value = latest
        syncDependencyFormFromSkill(latest)
      } else {
        currentSkill.value = null
        treeData.value = []
        resetFileState()
      }
    }
    await fetchDependencyOptions()
  } catch (error) {
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
  } catch {}
}

const syncDependencyFormFromSkill = (skillRecord) => {
  dependencyForm.tool_dependencies = [...(skillRecord?.tool_dependencies || [])]
  dependencyForm.mcp_dependencies = [...(skillRecord?.mcp_dependencies || [])]
  dependencyForm.skill_dependencies = [...(skillRecord?.skill_dependencies || [])]
}

const reloadTree = async () => {
  if (!currentSkill.value) return
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

const loadSkillFile = async (slug, path = 'SKILL.md') => {
  try {
    const fileResult = await skillApi.getSkillFile(slug, path)
    const content = fileResult?.data?.content || ''
    fileContent.value = content
    originalFileContent.value = content
    selectedPath.value = path
    selectedIsDir.value = false
    selectedTreeKeys.value = [path]
  } catch {
    // 文件不存在时忽略
  }
}

const selectSkill = async (record) => {
  currentSkill.value = record
  syncDependencyFormFromSkill(record)
  resetFileState()

  // 并行执行：加载树结构和获取 SKILL.md
  await Promise.all([reloadTree(), loadSkillFile(record.slug)])
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
    originalFileContent.value = ''
    return
  }
  try {
    const result = await skillApi.getSkillFile(currentSkill.value.slug, path)
    const content = result?.data?.content || ''
    fileContent.value = content
    originalFileContent.value = content
  } catch {
    message.error('文件读取失败')
  }
}

const saveCurrentFile = async () => {
  if (!currentSkill.value || !selectedPath.value || selectedIsDir.value) return
  savingFile.value = true
  try {
    await skillApi.updateSkillFile(currentSkill.value.slug, {
      path: selectedPath.value,
      content: fileContent.value
    })
    originalFileContent.value = fileContent.value
    message.success('已保存')
    if (selectedPath.value === 'SKILL.md') await fetchSkills()
  } catch {
    message.error('保存失败')
  } finally {
    savingFile.value = false
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
  if (!currentSkill.value || !createForm.path.trim()) return
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

const confirmDeleteNode = () => {
  if (!currentSkill.value || !selectedPath.value || selectedPath.value === 'SKILL.md') return
  Modal.confirm({
    title: '确认删除？',
    content: `将永久删除: ${selectedPath.value}`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await skillApi.deleteSkillFile(currentSkill.value.slug, selectedPath.value)
        resetFileState()
        await reloadTree()
        message.success('已删除')
      } catch {
        message.error('删除失败')
      }
    }
  })
}

const confirmDeleteSkill = () => {
  if (!currentSkill.value) return
  Modal.confirm({
    title: `彻底删除技能「${currentSkill.value.slug}」？`,
    content: '删除后无法恢复，所有文件和配置将永久消失。',
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await skillApi.deleteSkill(currentSkill.value.slug)
        message.success('已删除')
        currentSkill.value = null
        treeData.value = []
        resetFileState()
        await fetchSkills()
      } catch {
        message.error('删除失败')
      }
    }
  })
}

const handleExport = async () => {
  if (!currentSkill.value) return
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

const handleImportUpload = async ({ file, onSuccess, onError }) => {
  importing.value = true
  try {
    const result = await skillApi.importSkillZip(file)
    message.success('导入完成')
    await fetchSkills()
    const imported = result?.data
    if (imported?.slug) {
      const record = skills.value.find((i) => i.slug === imported.slug)
      if (record) await selectSkill(record)
    }
    onSuccess?.(result)
  } catch (e) {
    message.error('导入失败')
    onError?.(e)
  } finally {
    importing.value = false
  }
}

const saveDependencies = async () => {
  if (!currentSkill.value) return
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
    await fetchSkills()
    message.success('依赖已更新')
  } catch {
    message.error('更新失败')
  } finally {
    savingDependencies.value = false
  }
}

onMounted(fetchSkills)

// 暴露方法给父组件
defineExpose({
  fetchSkills,
  handleImportUpload
})
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.list-item {
  .item-details {
    .item-badges {
      display: flex;
      gap: 4px;
      .dot-badge {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        &.blue {
          background-color: #3b82f6;
        }
        &.green {
          background-color: #22c55e;
        }
      }
    }
  }
}

.workspace {
  display: flex;
  flex: 1;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

/* 文件 tree */
.tree-container {
  width: 240px;
  border-right: 1px solid @border-color;
  background-color: @bg-secondary;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;

  .tree-header {
    padding: 10px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid @border-color;
    background-color: var(--gray-50);
    .label {
      font-size: 11px;
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
    padding: 8px;
  }
}

/* 编辑器 */
.editor-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;

  .editor-header {
    padding: 8px 16px;
    border-bottom: 1px solid @border-color;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background-color: var(--gray-0);
    flex-shrink: 0;

    .current-path {
      display: flex;
      align-items: center;
      gap: 8px;
      font-family: monospace;
      font-size: 12px;
      color: var(--gray-500);
      .save-hint {
        color: #f59e0b;
        font-size: 10px;
        margin-left: 4px;
      }
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .view-toggle-btn {
      background-color: var(--gray-100);
      border-color: var(--gray-300);
      &:hover {
        background-color: var(--gray-200);
        border-color: var(--gray-400);
      }
    }
  }

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

  .editor-main :deep(textarea) {
    flex: 1;
    min-height: 0;
  }

  .pure-editor {
    width: 100%;
    height: 100%;
    border: none;
    resize: none;
    padding: 20px;
    font-family: 'Fira Code', 'Monaco', monospace;
    font-size: 13px;
    line-height: 1.6;
    &:focus {
      outline: none;
    }
  }

  .markdown-preview {
    flex: 1;
    height: 100%;
    overflow-y: auto;
    :deep(.md-editor) {
      height: 100%;
      background: var(--gray-0);
    }
    :deep(.md-editor-preview-wrapper) {
      padding: 16px 20px;
    }
  }
}

/* 依赖配置 */
.config-view {
  padding: 16px;
  flex: 1;
  overflow-y: auto;
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

@media (max-width: 1000px) {
  .sidebar-list {
    width: 220px;
  }
  .tree-container {
    width: 180px;
  }
}
</style>
