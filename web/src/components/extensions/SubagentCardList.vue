<template>
  <div class="subagent-cards-page extension-page-root">
    <PageShoulder search-placeholder="搜索 SubAgent..." v-model:search="searchQuery">
      <template #actions>
        <a-button type="primary" @click="handleSubagentAdd" class="lucide-icon-btn">
          <Plus :size="14" />
          <span>添加</span>
        </a-button>
        <a-tooltip title="刷新 Subagents">
          <a-button class="lucide-icon-btn" :disabled="loading" @click="fetchSubAgents">
            <RefreshCw :size="14" />
          </a-button>
        </a-tooltip>
      </template>
    </PageShoulder>

    <div
      v-if="filteredEnabledSubAgents.length === 0 && filteredDisabledSubAgents.length === 0"
      class="extension-card-grid-empty-state"
    >
      <a-empty :image="false" :description="searchQuery ? '无匹配 SubAgent' : '暂无 SubAgent'" />
    </div>

    <template v-else>
      <div v-if="filteredEnabledSubAgents.length" class="extension-section-header">已添加</div>
      <ExtensionCardGrid>
        <InfoCard
          v-for="agent in filteredEnabledSubAgents"
          :key="agent.name"
          :title="agent.name"
          :description="agent.description || '暂无描述'"
          :default-icon="BotIcon"
          :tags="agent.is_builtin ? [{ name: '内置' }] : [{ name: '添加' }]"
          :status="{ label: '已添加', level: 'success' }"
          @click="navigateToDetail(agent)"
        >
        </InfoCard>
      </ExtensionCardGrid>

      <div v-if="filteredDisabledSubAgents.length" class="extension-section-header">可添加</div>
      <ExtensionCardGrid v-if="filteredDisabledSubAgents.length">
        <InfoCard
          v-for="agent in filteredDisabledSubAgents"
          :key="agent.name"
          :title="agent.name"
          :description="agent.description || '暂无描述'"
          :default-icon="BotIcon"
          :tags="agent.is_builtin ? [{ name: '内置' }] : [{ name: '添加' }]"
          action-label="添加"
          @click="navigateToDetail(agent)"
          @action-click="handleSetAgentEnabled(agent, true)"
        >
        </InfoCard>
      </ExtensionCardGrid>
    </template>

    <a-modal
      v-model:open="formModalVisible"
      title="添加 SubAgent"
      @ok="handleFormSubmit"
      :confirmLoading="formLoading"
      @cancel="formModalVisible = false"
      :maskClosable="false"
      width="600px"
    >
      <a-form layout="vertical" class="extension-form">
        <a-form-item label="名称" required class="form-item">
          <a-input v-model:value="form.name" placeholder="请输入 SubAgent 名称（唯一标识）" />
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
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Plus, RefreshCw, Bot } from 'lucide-vue-next'
import { subagentApi } from '@/apis/subagent_api'
import { useSubagentOptions } from '@/composables/useSubagentOptions'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'

const BotIcon = Bot

const router = useRouter()

const loading = ref(false)
const subagents = ref([])
const searchQuery = ref('')

const {
  availableTools,
  availableMcps,
  availableSkills,
  fetchAvailableTools,
  fetchAvailableMcps,
  fetchAvailableSkills
} = useSubagentOptions()

const formModalVisible = ref(false)
const formLoading = ref(false)
const form = reactive({
  name: '',
  description: '',
  system_prompt: '',
  tools: [],
  mcps: [],
  skills: [],
  model: ''
})

const getSortedSubAgents = (items) => {
  return [...items].sort((a, b) => {
    if (a.is_builtin !== b.is_builtin) return a.is_builtin ? -1 : 1
    return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  })
}

const filteredSubAgents = computed(() => {
  const sorted = getSortedSubAgents(subagents.value)
  if (!searchQuery.value) return sorted
  const q = searchQuery.value.toLowerCase()
  return sorted.filter(
    (a) => a.name.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q)
  )
})

const filteredEnabledSubAgents = computed(() =>
  filteredSubAgents.value.filter((item) => item.enabled !== false)
)
const filteredDisabledSubAgents = computed(() =>
  filteredSubAgents.value.filter((item) => item.enabled === false)
)

const navigateToDetail = (agent) => {
  router.push({ path: `/extensions/subagent/${encodeURIComponent(agent.name)}` })
}

const handleSubagentAdd = () => {
  Object.assign(form, {
    name: '',
    description: '',
    system_prompt: '',
    tools: [],
    mcps: [],
    skills: [],
    model: ''
  })
  formModalVisible.value = true
}

const handleModelSelect = (spec) => {
  form.model = spec || ''
}

const handleFormSubmit = async () => {
  try {
    if (!form.name?.trim()) {
      message.error('名称不能为空')
      return
    }
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
    const result = await subagentApi.createSubAgent(data)
    if (result.success) {
      message.success('SubAgent 创建成功')
      formModalVisible.value = false
      await fetchSubAgents()
    } else {
      message.error(result.message || '创建失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    formLoading.value = false
  }
}

const handleSetAgentEnabled = async (agent, enabled) => {
  try {
    const result = await subagentApi.updateSubAgentStatus(agent.name, enabled)
    if (result.success) {
      message.success(result.message || `SubAgent 已${enabled ? '添加' : '移除'}`)
      await fetchSubAgents()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  }
}

const fetchSubAgents = async () => {
  try {
    loading.value = true
    const result = await subagentApi.getSubAgents()
    if (result.success) {
      subagents.value = result.data || []
    }
  } catch (err) {
    message.error(err.message || '获取列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchSubAgents()
})

defineExpose({ fetchSubAgents, loading })
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

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
