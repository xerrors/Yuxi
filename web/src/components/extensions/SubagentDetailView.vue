<template>
  <div class="subagent-detail extension-detail-page">
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
          <Bot :size="18" />
        </div>
        <div class="detail-title-text">
          <h2>{{ agent?.name || name }}</h2>
          <span class="detail-subtitle">{{ agent?.is_builtin ? '内置' : '自定义' }}</span>
        </div>
      </div>
      <div class="detail-actions">
        <a-space :size="8">
          <button
            type="button"
            @click="showEditModal"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
            v-if="agent && !agent.is_builtin"
          >
            <Pencil :size="14" />
            <span>编辑</span>
          </button>
          <button
            type="button"
            danger
            :disabled="!agent || agent.is_builtin"
            @click="confirmDeleteAgent"
            class="lucide-icon-btn extension-panel-action extension-panel-action-danger"
            v-if="agent && !agent.is_builtin"
          >
            <Trash2 :size="14" />
            <span>删除</span>
          </button>
        </a-space>
      </div>
    </div>

    <div class="detail-content-wrapper">
      <a-spin :spinning="loading">
        <div v-if="agent" class="detail-content-inner">
          <div class="detail-section-container">
            <div class="detail-section">
              <div class="section-header">
                <MessageSquare :size="14" />
                <span>系统提示词</span>
              </div>
              <div class="section-content">
                <div class="code-panel">
                  <pre class="code-panel-pre">{{ agent.system_prompt }}</pre>
                </div>
              </div>
            </div>

            <div class="detail-section" v-if="agent.description">
              <div class="section-header">
                <FileText :size="14" />
                <span>描述</span>
              </div>
              <div class="section-content description">
                {{ agent.description }}
              </div>
            </div>

            <div class="detail-section" v-if="agent.model">
              <div class="section-header">
                <Cpu :size="14" />
                <span>模型覆盖</span>
              </div>
              <div class="section-content">
                {{ agent.model }}
              </div>
            </div>

            <div class="detail-section" v-if="agent.tools && agent.tools.length > 0">
              <div class="section-header">
                <Wrench :size="14" />
                <span>工具</span>
              </div>
              <div class="section-content">
                <a-tag v-for="tool in agent.tools" :key="tool">{{ tool }}</a-tag>
              </div>
            </div>

            <div class="detail-section" v-if="agent.mcps && agent.mcps.length > 0">
              <div class="section-header">
                <Server :size="14" />
                <span>MCP 服务器</span>
              </div>
              <div class="section-content">
                <a-tag v-for="mcp in agent.mcps" :key="mcp" color="blue">{{ mcp }}</a-tag>
              </div>
            </div>

            <div class="detail-section" v-if="agent.skills && agent.skills.length > 0">
              <div class="section-header">
                <Sparkles :size="14" />
                <span>技能 (Skills)</span>
              </div>
              <div class="section-content">
                <a-tag v-for="skill in agent.skills" :key="skill" color="purple">{{ skill }}</a-tag>
              </div>
            </div>

            <div class="detail-section" v-if="agent.is_builtin || agent.enabled === false">
              <div class="section-header">
                <Info :size="14" />
                <span>类型</span>
              </div>
              <div class="section-content">
                <a-tag v-if="agent.is_builtin" color="blue">内置</a-tag>
                <a-tag v-else color="default">自定义</a-tag>
                <a-tag v-if="agent.enabled === false" color="default">未添加</a-tag>
              </div>
            </div>

            <div class="detail-section">
              <div class="section-header">
                <Clock :size="14" />
                <span>元信息</span>
              </div>
              <div class="section-content meta-info">
                <div class="meta-item">
                  <span class="meta-label">创建时间</span>
                  <span class="meta-value">{{ formatTime(agent.created_at) }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">更新时间</span>
                  <span class="meta-value">{{ formatTime(agent.updated_at) }}</span>
                </div>
                <div class="meta-item" v-if="agent.created_by">
                  <span class="meta-label">创建人</span>
                  <span class="meta-value">{{ agent.created_by }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div v-else-if="!loading" class="detail-empty">
          <a-empty description="未找到 SubAgent" />
        </div>
      </a-spin>
    </div>

    <a-modal
      v-model:open="formModalVisible"
      title="编辑 SubAgent"
      @ok="handleFormSubmit"
      :confirmLoading="formLoading"
      @cancel="formModalVisible = false"
      :maskClosable="false"
      width="600px"
    >
      <a-form layout="vertical" class="extension-form">
        <a-form-item label="名称" required class="form-item">
          <a-input
            v-model:value="form.name"
            placeholder="请输入 SubAgent 名称（唯一标识）"
            disabled
          />
        </a-form-item>
        <a-form-item label="描述" class="form-item">
          <a-input v-model:value="form.description" placeholder="请输入 SubAgent 描述" />
        </a-form-item>
        <a-form-item label="系统提示词" required class="form-item">
          <a-textarea v-model:value="form.system_prompt" placeholder="请输入系统提示词" :rows="6" />
        </a-form-item>
        <a-form-item label="工具" class="form-item">
          <a-select
            v-model:value="form.tools"
            mode="tags"
            placeholder="选择或输入工具名称"
            style="width: 100%"
            :options="availableTools"
            @focus="fetchAvailableTools"
          />
        </a-form-item>
        <a-form-item label="MCP服务器" class="form-item">
          <a-select
            v-model:value="form.mcps"
            mode="tags"
            placeholder="选择或输入启用子代理专属 MCP 服务器"
            style="width: 100%"
            :options="availableMcps"
            @focus="fetchAvailableMcps"
          />
        </a-form-item>
        <a-form-item label="技能（Skills）" class="form-item">
          <a-select
            v-model:value="form.skills"
            mode="tags"
            placeholder="选择或输入子代理专属技能"
            style="width: 100%"
            :options="availableSkills"
            @focus="fetchAvailableSkills"
          />
        </a-form-item>
        <a-form-item label="模型覆盖（可选）" class="form-item">
          <div class="model-override-row">
            <ModelSelectorComponent
              :model_spec="form.model"
              placeholder="请选择模型"
              class="model-selector-full"
              @select-model="handleModelSelect"
            />
            <a-button v-if="form.model" type="link" size="small" @click="form.model = ''"
              >清空</a-button
            >
          </div>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  ArrowLeft,
  Bot,
  Pencil,
  Trash2,
  Info,
  MessageSquare,
  FileText,
  Cpu,
  Wrench,
  Clock,
  Server,
  Sparkles
} from 'lucide-vue-next'
import { subagentApi } from '@/apis/subagent_api'
import { useSubagentOptions } from '@/composables/useSubagentOptions'
import { formatFullDateTime } from '@/utils/time'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'

const route = useRoute()
const router = useRouter()
const name = computed(() => decodeURIComponent(route.params.name))

const loading = ref(false)
const agent = ref(null)

const formModalVisible = ref(false)
const formLoading = ref(false)

const {
  availableTools,
  availableMcps,
  availableSkills,
  fetchAvailableTools,
  fetchAvailableMcps,
  fetchAvailableSkills
} = useSubagentOptions()
const form = reactive({
  name: '',
  description: '',
  system_prompt: '',
  tools: [],
  mcps: [],
  skills: [],
  model: ''
})

const goBack = () => {
  router.push({ path: '/extensions', query: { tab: 'subagents' } })
}

const formatTime = (timeStr) => formatFullDateTime(timeStr)

const fetchAgent = async () => {
  try {
    loading.value = true
    const result = await subagentApi.getSubAgent(name.value)
    if (result.success && result.data) {
      agent.value = result.data
    } else {
      message.error(result.message || '获取 SubAgent 详情失败')
    }
  } catch (err) {
    message.error(err.message || '获取 SubAgent 详情失败')
  } finally {
    loading.value = false
  }
}

const showEditModal = () => {
  if (!agent.value) return
  Object.assign(form, {
    name: agent.value.name,
    description: agent.value.description || '',
    system_prompt: agent.value.system_prompt || '',
    tools: agent.value.tools || [],
    mcps: agent.value.mcps || [],
    skills: agent.value.skills || [],
    model: agent.value.model || ''
  })
  formModalVisible.value = true
}

const handleModelSelect = (spec) => {
  form.model = spec || ''
}

const handleFormSubmit = async () => {
  try {
    if (!form.system_prompt?.trim()) {
      message.error('系统提示词不能为空')
      return
    }
    formLoading.value = true
    const data = {
      name: form.name.trim(),
      description: form.description || '',
      system_prompt: form.system_prompt,
      tools: form.tools || [],
      mcps: form.mcps || [],
      skills: form.skills || [],
      model: form.model || null
    }
    const result = await subagentApi.updateSubAgent(form.name, data)
    if (result.success) {
      message.success('SubAgent 更新成功')
      formModalVisible.value = false
      await fetchAgent()
    } else {
      message.error(result.message || '更新失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    formLoading.value = false
  }
}

const confirmDeleteAgent = () => {
  if (!agent.value) return
  Modal.confirm({
    title: '确认删除 SubAgent',
    content: `确定要删除 SubAgent "${agent.value.name}" 吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const result = await subagentApi.deleteSubAgent(agent.value.name)
        if (result.success) {
          message.success('SubAgent 删除成功')
          router.push({ path: '/extensions', query: { tab: 'subagents' } })
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (err) {
        message.error(err.message || '删除失败')
      }
    }
  })
}

onMounted(() => {
  fetchAgent()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
@import '@/assets/css/extension-detail.less';

.subagent-detail {
  .detail-content-wrapper {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    background-color: var(--gray-10);
  }

  .detail-content-inner {
    max-width: 720px;
    margin: 0 auto;
    padding: 16px var(--page-padding);
  }
}

.model-override-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.model-selector-full {
  flex: 1;

  :deep(.model-select) {
    width: 100%;
  }
}
</style>
