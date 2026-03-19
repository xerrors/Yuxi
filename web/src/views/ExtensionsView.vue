<template>
  <div class="extensions-view extension-page-root">
    <div class="extensions-header">
      <a-tabs v-model:activeKey="activeTab" class="extensions-tabs">
        <a-tab-pane key="tools" tab="工具" />
        <a-tab-pane key="skills" tab="Skills 管理" />
        <a-tab-pane key="mcp" tab="MCP 服务器" />
        <a-tab-pane key="subagents" tab="Subagents 管理" />
      </a-tabs>
      <div class="header-actions">
        <!-- Skills Tab 的按钮 -->
        <template v-if="activeTab === 'skills'">
          <a-upload
            accept=".zip"
            :show-upload-list="false"
            :custom-request="handleImportUpload"
            :disabled="skillsLoading || skillsImporting"
          >
            <a-button type="primary" :loading="skillsImporting" class="lucide-icon-btn">
              <Upload :size="14" />
              <span>导入 ZIP</span>
            </a-button>
          </a-upload>
          <a-button @click="handleSkillsRefresh" :disabled="skillsLoading" class="lucide-icon-btn">
            <RotateCw :size="14" />
            <span>刷新</span>
          </a-button>
        </template>
        <!-- Tools Tab 的按钮 -->
        <template v-else-if="activeTab === 'tools'">
          <a-button @click="handleToolsRefresh" :disabled="toolsLoading" class="lucide-icon-btn">
            <RotateCw :size="14" />
            <span>刷新</span>
          </a-button>
        </template>
        <!-- MCP Tab 的按钮 -->
        <template v-else-if="activeTab === 'mcp'">
          <a-button type="primary" @click="handleMcpAdd" class="lucide-icon-btn">
            <Plus :size="14" />
            <span>添加服务器</span>
          </a-button>
          <a-button @click="handleMcpRefresh" :disabled="mcpLoading" class="lucide-icon-btn">
            <RotateCw :size="14" />
            <span>刷新</span>
          </a-button>
        </template>
        <!-- Subagents Tab 的按钮 -->
        <template v-else-if="activeTab === 'subagents'">
          <a-button type="primary" @click="handleSubagentAdd" class="lucide-icon-btn">
            <Plus :size="14" />
            <span>添加</span>
          </a-button>
          <a-button
            @click="handleSubagentRefresh"
            :disabled="subagentsLoading"
            class="lucide-icon-btn"
          >
            <RotateCw :size="14" />
            <span>刷新</span>
          </a-button>
        </template>
      </div>
    </div>

    <div class="extensions-content">
      <div v-show="activeTab === 'tools'" class="tab-panel">
        <ToolsManagerComponent ref="toolsRef" @refresh="handleToolsRefresh" />
      </div>
      <div v-show="activeTab === 'skills'" class="tab-panel">
        <SkillsManagerComponent
          ref="skillsRef"
          @import="handleSkillsImport"
          @refresh="handleSkillsRefresh"
        />
      </div>
      <div v-show="activeTab === 'mcp'" class="tab-panel">
        <McpServersComponent ref="mcpRef" @add="handleMcpAdd" @refresh="handleMcpRefresh" />
      </div>
      <div v-show="activeTab === 'subagents'" class="tab-panel">
        <SubAgentsComponent
          ref="subagentsRef"
          @add="handleSubagentAdd"
          @refresh="handleSubagentRefresh"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Upload, RotateCw, Plus } from 'lucide-vue-next'
import SkillsManagerComponent from '@/components/SkillsManagerComponent.vue'
import ToolsManagerComponent from '@/components/ToolsManagerComponent.vue'
import McpServersComponent from '@/components/McpServersComponent.vue'
import SubAgentsComponent from '@/components/SubAgentsComponent.vue'

const route = useRoute()
const activeTab = ref('tools')
const skillsRef = ref(null)

// 监听路由 query 参数变化
watch(
  () => route.query,
  (query) => {
    if (query.tab && ['tools', 'skills', 'mcp', 'subagents'].includes(query.tab)) {
      activeTab.value = query.tab
    }
  },
  { immediate: true }
)
const toolsRef = ref(null)
const mcpRef = ref(null)
const subagentsRef = ref(null)

// Skills 相关状态（从子组件透传）
const skillsLoading = ref(false)
const skillsImporting = ref(false)
const toolsLoading = ref(false)
const mcpLoading = ref(false)
const subagentsLoading = ref(false)

// 暴露给子组件的状态更新
const updateSkillsState = (loading, importing) => {
  skillsLoading.value = loading
  skillsImporting.value = importing
}

const updateToolsState = (loading) => {
  toolsLoading.value = loading
}

const updateMcpState = (loading) => {
  mcpLoading.value = loading
}

const updateSubagentsState = (loading) => {
  subagentsLoading.value = loading
}

// Skills 事件处理
const handleSkillsImport = () => {
  // 导入完成后自动刷新
  handleSkillsRefresh()
}

const handleSkillsRefresh = () => {
  if (skillsRef.value?.fetchSkills) {
    updateSkillsState(true, skillsImporting.value)
    skillsRef.value.fetchSkills().finally(() => {
      updateSkillsState(false, skillsImporting.value)
    })
  }
}

// Tools 事件处理
const handleToolsRefresh = () => {
  if (toolsRef.value?.fetchTools) {
    updateToolsState(true)
    toolsRef.value.fetchTools().finally(() => {
      updateToolsState(false)
    })
  }
}

// MCP 事件处理
const handleMcpAdd = () => {
  if (mcpRef.value?.showAddModal) {
    mcpRef.value.showAddModal()
  }
}

const handleMcpRefresh = () => {
  if (mcpRef.value?.fetchServers) {
    updateMcpState(true)
    mcpRef.value.fetchServers().finally(() => {
      updateMcpState(false)
    })
  }
}

// Subagents 事件处理
const handleSubagentAdd = () => {
  if (subagentsRef.value?.showAddModal) {
    subagentsRef.value.showAddModal()
  }
}

const handleSubagentRefresh = () => {
  if (subagentsRef.value?.fetchSubAgents) {
    updateSubagentsState(true)
    subagentsRef.value.fetchSubAgents().finally(() => {
      updateSubagentsState(false)
    })
  }
}

// 处理导入上传
const handleImportUpload = async ({ file, onSuccess, onError }) => {
  if (skillsRef.value?.handleImportUpload) {
    updateSkillsState(skillsLoading.value, true)
    try {
      await skillsRef.value.handleImportUpload({ file, onSuccess, onError })
      handleSkillsImport()
    } catch (e) {
      onError?.(e)
    } finally {
      updateSkillsState(skillsLoading.value, false)
    }
  }
}
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.extensions-view {
  .extensions-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 16px;
    border-bottom: 1px solid var(--gray-150);
    background-color: var(--gray-0);

    .extensions-tabs {
      flex: 1;
      height: auto;
      display: flex;
      flex-direction: column;

      :deep(.ant-tabs-nav) {
        margin: 0;
        padding: 0;

        &::before {
          border-bottom: none;
        }
      }

      :deep(.ant-tabs-nav::after) {
        content: none;
      }

      :deep(.ant-tabs-nav-left-bar) {
        display: none;
      }

      :deep(.ant-tabs-items) {
        padding: 0;
      }

      :deep(.ant-tabs-tab) {
        padding: 12px 16px;
        font-size: 14px;
        margin: 0;
      }

      :deep(.ant-tabs-ink-bar) {
        display: block;
      }
    }

    .header-actions {
      display: flex;
      gap: 8px;
      padding: 8px 0;
    }
  }

  .extensions-content {
    flex: 1;
    min-height: 0;
    overflow: hidden;

    .tab-panel {
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
  }
}
</style>
