<template>
  <a-spin wrapper-class-name="basic-settings-section settings-page" :spinning="saving" tip="保存中…">
    <template v-if="userStore.isSuperAdmin">
      <section class="settings-section-block">
        <h3 class="settings-section-title">默认项配置</h3>
        <section class="settings-form-panel" aria-label="默认模型配置">
          <div class="settings-form-row">
            <div class="settings-form-copy">
              <div class="settings-form-label">
                {{ items?.default_model?.des || '默认对话模型' }}
              </div>
              <p class="settings-form-description">未单独指定模型时，用于对话与内容生成。</p>
            </div>
            <div class="settings-form-control">
              <ModelSelectorComponent
                display-name="mini"
                @select-model="handleChatModelSelect"
                :model_spec="configStore.config?.default_model"
                placeholder="请选择默认模型"
              />
            </div>
          </div>
          <div v-if="personalAssistantEnabled" class="settings-form-row">
            <div class="settings-form-copy">
              <div class="settings-form-label">个人助理模型</div>
              <p class="settings-form-description">用于所有个人助理；未配置时可以创建助理，但无法发送消息。</p>
            </div>
            <div class="settings-form-control">
              <ModelSelectorComponent
                display-name="mini"
                :model_spec="configStore.config?.personal_assistant_model || ''"
                :disabled="personalModelSaving"
                placeholder="请选择个人助理模型"
                @select-model="savePersonalModel"
              />
              <p v-if="personalModelError" role="alert">{{ personalModelError }}</p>
            </div>
          </div>
          <div class="settings-form-row">
            <div class="settings-form-copy">
              <div class="settings-form-label">{{ items?.fast_model?.des || '快速响应模型' }}</div>
              <p class="settings-form-description">用于会话标题等需要快速响应的轻量任务。</p>
            </div>
            <div class="settings-form-control">
              <ModelSelectorComponent
                display-name="mini"
                @select-model="handleFastModelSelect"
                :model_spec="configStore.config?.fast_model"
                placeholder="请选择模型"
              />
            </div>
          </div>
          <div class="settings-form-row">
            <div class="settings-form-copy">
              <div class="settings-form-label">
                {{ items?.reference_model?.des || '来源标注模型' }}
              </div>
              <p class="settings-form-description">
                建议选择非思考模型，或在模型配置中关闭思考、降低思考强度。
              </p>
            </div>
            <div class="settings-form-control">
              <ModelSelectorComponent
                display-name="mini"
                :model_spec="configStore.config?.reference_model || ''"
                clearable
                placeholder="沿用快速响应模型"
                @select-model="handleChange('reference_model', $event || '')"
              />
            </div>
          </div>
          <div class="settings-form-row">
            <div class="settings-form-copy">
              <div class="settings-form-label">
                {{ items?.embed_model?.des || '默认 Embedding 模型' }}
              </div>
              <p class="settings-form-description">将文本转为向量，用于知识库索引与检索。</p>
            </div>
            <div class="settings-form-control is-compact">
              <EmbeddingModelSelector
                display-name="mini"
                :value="configStore.config?.embed_model"
                @change="handleChange('embed_model', $event)"
              />
            </div>
          </div>
          <div class="settings-form-row">
            <div class="settings-form-copy">
              <div class="settings-form-label">
                {{ items?.reranker?.des || '默认 Re-Ranker 模型' }}
              </div>
              <p class="settings-form-description">对检索结果重排序，优先呈现更相关的内容。</p>
            </div>
            <div class="settings-form-control is-compact">
              <RerankModelSelector
                display-name="mini"
                :value="configStore.config?.reranker"
                @change="handleChange('reranker', $event)"
              />
            </div>
          </div>
        </section>
      </section>

      <section class="settings-section-block">
        <h3 class="settings-section-title">协作限制</h3>
        <section class="settings-form-panel">
          <div class="settings-form-row">
            <div class="settings-form-copy">
              <label class="settings-form-label" for="max-cooperation-depth">最大协作深度</label>
              <p class="settings-form-description" id="cooperation-depth-help">
                根对话为第 0 层，默认允许 2 层协作；设为 0 可关闭新增子会话。
              </p>
            </div>
            <div class="settings-form-control is-number">
              <a-input-number
                id="max-cooperation-depth"
                aria-describedby="cooperation-depth-help"
                :value="configStore.config?.max_cooperation_depth ?? 2"
                :disabled="saving"
                :min="0"
                :precision="0"
                @change="handleCooperationDepthChange"
              />
            </div>
          </div>
          <div class="settings-form-row">
            <div class="settings-form-copy">
              <label class="settings-form-label" for="max-cooperation-concurrency">协作树并发执行上限</label>
              <p class="settings-form-description" id="cooperation-concurrency-help">
                每棵协作树（含根会话）同时处于执行中的任务上限，默认 4；排队与等待中的任务不占名额。
              </p>
            </div>
            <div class="settings-form-control is-number">
              <a-input-number
                id="max-cooperation-concurrency"
                aria-describedby="cooperation-concurrency-help"
                :value="configStore.config?.max_cooperation_concurrency ?? 4"
                :disabled="saving"
                :min="1"
                :precision="0"
                @change="handleCooperationConcurrencyChange"
              />
            </div>
          </div>
        </section>
      </section>

      <SkillSettingsSection />
    </template>
  </a-spin>
</template>

<script setup>
import { message } from 'ant-design-vue'
import { computed, onMounted, ref } from 'vue'
import { configApi, healthApi } from '@/apis/system_api'
import { useConfigStore } from '@/modules/settings/model/config'
import {
  COOPERATION_CONCURRENCY_KEY,
  COOPERATION_DEPTH_KEY,
  applyCooperationConcurrency,
  applyCooperationDepth
} from '@/modules/settings/model/cooperationDepth'
import { useUserStore } from '@/modules/identity'
import ModelSelectorComponent from '@/modules/agents/ui/ModelSelectorComponent.vue'
import EmbeddingModelSelector from '@/modules/settings/ui/EmbeddingModelSelector.vue'
import RerankModelSelector from '@/modules/settings/ui/RerankModelSelector.vue'
import SkillSettingsSection from '@/modules/agents/ui/SkillSettingsSection.vue'

const configStore = useConfigStore()
const userStore = useUserStore()
const personalAssistantEnabled = ref(false)
const personalModelSaving = ref(false)
const personalModelError = ref('')
onMounted(async () => {
  try {
    const discovery = await healthApi.getDiscovery()
    personalAssistantEnabled.value = discovery?.capabilities?.features?.personal_assistant === true
  } catch {
    personalAssistantEnabled.value = false
  }
})

/** 保存后以服务端配置刷新选择，不用乐观值代替最终结果。 */
async function savePersonalModel(spec) {
  personalModelSaving.value = true
  personalModelError.value = ''
  try {
    configStore.setConfig(await configApi.updateConfigBatch({ personal_assistant_model: spec || '' }))
  } catch (error) {
    personalModelError.value = error.message || '个人助理模型保存失败，请重试。'
  } finally {
    personalModelSaving.value = false
  }
}

const items = computed(() => configStore.config?._config_items || {})
const saving = ref(false)
const handleChange = async (key, value) => {
  if (saving.value) return
  saving.value = true
  try {
    await configStore.setConfigValue(key, value)
  } catch (error) {
    message.error(error.message || '保存设置失败')
  } finally {
    saving.value = false
  }
}

/** 只提交完整的非负整数，清空输入不会修改已保存的限制。 */
const handleCooperationDepthChange = (value) => {
  applyCooperationDepth(value, (depth) => handleChange(COOPERATION_DEPTH_KEY, depth))
}

/** 只提交不小于 1 的整数，清空输入不会修改已保存的上限。 */
const handleCooperationConcurrencyChange = (value) => {
  applyCooperationConcurrency(value, (limit) => handleChange(COOPERATION_CONCURRENCY_KEY, limit))
}

const handleChatModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    handleChange('default_model', spec)
  }
}

const handleFastModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    handleChange('fast_model', spec)
  }
}
</script>
