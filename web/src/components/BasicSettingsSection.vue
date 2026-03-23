<template>
  <div class="basic-settings-section">
    <template v-if="userStore.isSuperAdmin">
      <div class="section-title">检索配置</div>
      <div class="settings-panel">
        <div class="setting-row two-cols">
          <div class="col-item">
            <div class="setting-label">{{ items?.default_model?.des || '默认对话模型' }}</div>
            <div class="setting-content">
              <ModelSelectorComponent
                @select-model="handleChatModelSelect"
                :model_spec="configStore.config?.default_model"
                placeholder="请选择默认模型"
              />
            </div>
          </div>
          <div class="col-item">
            <div class="setting-label">{{ items?.fast_model?.des }}</div>
            <div class="setting-content">
              <ModelSelectorComponent
                @select-model="handleFastModelSelect"
                :model_spec="configStore.config?.fast_model"
                placeholder="请选择模型"
              />
            </div>
          </div>
        </div>
        <div class="setting-row two-cols">
          <div class="col-item">
            <div class="setting-label">{{ items?.embed_model?.des }}</div>
            <div class="setting-content">
              <EmbeddingModelSelector
                :value="configStore.config?.embed_model"
                @change="handleChange('embed_model', $event)"
                style="width: 100%"
              />
            </div>
          </div>
          <div class="col-item">
            <div class="setting-label">{{ items?.reranker?.des }}</div>
            <div class="setting-content">
              <a-select
                class="full-width"
                :value="configStore.config?.reranker"
                @change="handleChange('reranker', $event)"
                placeholder="请选择重排序模型"
              >
                <a-select-option v-for="(name, idx) in rerankerChoices" :key="idx" :value="name"
                  >{{ name }}
                </a-select-option>
              </a-select>
            </div>
          </div>
        </div>
      </div>

      <div class="section-title">内容审查配置</div>
      <div class="section">
        <div class="card">
          <span class="label">{{ items?.enable_content_guard?.des }}</span>
          <a-switch
            :checked="configStore.config?.enable_content_guard"
            @change="handleChange('enable_content_guard', $event)"
          />
        </div>
        <div class="card" v-if="configStore.config?.enable_content_guard">
          <span class="label">{{ items?.enable_content_guard_llm?.des }}</span>
          <a-switch
            :checked="configStore.config?.enable_content_guard_llm"
            @change="handleChange('enable_content_guard_llm', $event)"
          />
        </div>
        <div
          class="card card-select"
          v-if="
            configStore.config?.enable_content_guard && configStore.config?.enable_content_guard_llm
          "
        >
          <span class="label">{{ items?.content_guard_llm_model?.des }}</span>
          <ModelSelectorComponent
            @select-model="handleContentGuardModelSelect"
            :model_spec="configStore.config?.content_guard_llm_model"
            placeholder="请选择模型"
          />
        </div>
      </div>
    </template>

    <div v-if="userStore.isAdmin" class="section-title">智能体配置</div>
    <div v-if="userStore.isAdmin" class="section">
      <div class="card card-select agent-card">
        <div class="label-group">
          <span class="label">默认智能体</span>
          <span class="helper-text">用于默认进入的智能体，也作为新会话的默认选择。</span>
        </div>
        <a-select
          class="agent-select"
          :value="agentStore.defaultAgentId"
          :options="agentOptions"
          :loading="isSettingDefaultAgent"
          :disabled="isSettingDefaultAgent || !agentOptions.length"
          placeholder="请选择默认智能体"
          @change="handleDefaultAgentChange"
        />
      </div>
    </div>

    <!-- 服务链接部分 -->
    <div v-if="userStore.isAdmin" class="section-title">服务链接</div>
    <div v-if="userStore.isAdmin">
      <p class="service-description">
        快速访问系统相关的外部服务，需要将 localhost 替换为实际的 IP 地址。
      </p>
      <div class="services-grid">
        <div class="service-link-card">
          <div class="service-info">
            <h4>Neo4j 浏览器</h4>
            <p>图数据库管理界面</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:7474/')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>API 接口文档</h4>
            <p>系统接口文档和调试工具</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:5050/docs')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>MinIO 对象存储</h4>
            <p>文件存储管理控制台</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:9001')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>Milvus WebUI</h4>
            <p>向量数据库管理界面</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:9091/webui/')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useConfigStore } from '@/stores/config'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { Globe } from 'lucide-vue-next'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'

const configStore = useConfigStore()
const agentStore = useAgentStore()
const userStore = useUserStore()
const items = computed(() => configStore.config?._config_items || {})
const isSettingDefaultAgent = ref(false)

const rerankerChoices = computed(() => {
  return Object.keys(configStore?.config?.reranker_names || {}) || []
})

const agentOptions = computed(() =>
  (agentStore.agents || []).map((agent) => ({
    label: agent.name || 'Unknown',
    value: agent.id
  }))
)

const handleChange = (key, e) => {
  configStore.setConfigValue(key, e)
}

const handleChatModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('default_model', spec)
  }
}

const handleFastModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('fast_model', spec)
  }
}

const handleContentGuardModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('content_guard_llm_model', spec)
  }
}

const handleDefaultAgentChange = async (agentId) => {
  if (!agentId || agentId === agentStore.defaultAgentId) return

  isSettingDefaultAgent.value = true
  try {
    await agentStore.setDefaultAgent(agentId)
    message.success('默认智能体已更新')
  } finally {
    isSettingDefaultAgent.value = false
  }
}

const openLink = (url) => {
  window.open(url, '_blank')
}

onMounted(async () => {
  if (!agentStore.agents.length) {
    await agentStore.fetchAgents()
  }
  if (!agentStore.defaultAgentId) {
    await agentStore.fetchDefaultAgent()
  }
})
</script>

<style lang="less" scoped>
.basic-settings-section {
  .settings-content {
    max-width: 100%;
  }

  .section-title {
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 600;
    margin: 24px 0 0 0;
    padding-bottom: 8px;

    &:first-child {
      margin-top: 12px;
    }
  }

  .service-description {
    color: var(--gray-600);
    font-size: 14px;
    margin: 0 0 16px 0;
    line-height: 1.5;
  }

  .section {
    background-color: var(--gray-0);
    padding: 10px 16px;
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    border: 1px solid var(--gray-150);
  }

  .settings-panel {
    background-color: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .setting-row {
    display: flex;
    flex-direction: column;
    gap: 8px;

    &.two-cols {
      flex-direction: row;
      gap: 20px;
    }

    .col-item {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }
  }

  .setting-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-700);
  }

  .setting-content {
    width: 100%;

    .full-width {
      width: 100%;
    }
  }

  .card {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .label {
      margin-right: 20px;
      font-weight: 500;
      color: var(--gray-800);
      flex-shrink: 0;
      min-width: 140px;
    }

    &.card-select {
      align-items: flex-start;
      gap: 12px;

      .label {
        margin-right: 0;
        margin-top: 6px;
      }
    }
  }

  .agent-card {
    align-items: center;
  }

  .label-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .helper-text {
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.5;
  }

  .agent-select {
    width: 320px;
    max-width: 100%;
  }

  .services-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 12px;
    margin-top: 16px;
  }

  .service-link-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    transition: all 0.2s;
    min-height: 70px;

    &:hover {
      box-shadow: 0 1px 8px var(--gray-150);
      border-color: var(--gray-100);
    }

    .service-info {
      flex: 1;
      margin-right: 16px;

      h4 {
        margin: 0 0 4px 0;
        color: var(--gray-900);
        font-size: 15px;
        font-weight: 500;
      }

      p {
        margin: 0;
        color: var(--gray-600);
        font-size: 13px;
        line-height: 1.4;
      }
    }
  }

  @media (max-width: 768px) {
    .agent-card {
      align-items: flex-start;
      flex-direction: column;
    }

    .agent-select {
      width: 100%;
    }
  }
}
</style>
