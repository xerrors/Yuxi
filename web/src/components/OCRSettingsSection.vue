<template>
  <div class="ocr-settings-section">
    <div class="section-title">默认 OCR 方法</div>
    <div class="settings-panel">
      <div class="setting-label">{{ items?.default_ocr_engine?.des || '默认 OCR 方法' }}</div>
      <OCRSelector
        :model-value="configStore.config?.default_ocr_engine"
        @update:model-value="configStore.setConfigValue('default_ocr_engine', $event)"
      />
    </div>

    <div class="section-title">OCR 服务配置</div>
    <p class="section-description">
      仅展示需要配置的服务。保存空值会清除数据库配置，并读取对应环境变量。
    </p>
    <div class="option-list">
      <section v-for="option in configOptions" :key="option.key" class="option-card">
        <header class="option-header">
          <div>
            <h4>{{ option.name }}</h4>
            <p>{{ option.description }}</p>
          </div>
          <div class="option-actions">
            <template v-if="editingKey === option.key">
              <a-button size="small" :disabled="savingOption === option.key" @click="cancelEditing">
                取消
              </a-button>
              <a-button
                type="primary"
                size="small"
                class="save-button"
                :loading="savingOption === option.key"
                @click="saveOption(option)"
              >
                保存
              </a-button>
            </template>
            <a-button
              v-else
              size="small"
              :disabled="Boolean(editingKey)"
              @click="startEditing(option)"
            >
              编辑
            </a-button>
          </div>
        </header>
        <div v-if="editingKey === option.key" class="option-fields">
          <label v-for="field in option.params.fields" :key="field.key" class="option-field">
            <span class="setting-label">{{ field.label }}</span>
            <a-input-password
              v-if="field.sensitive"
              v-model:value="draftValue[field.key]"
              :placeholder="field.environment"
              autocomplete="new-password"
            />
            <a-input
              v-else
              v-model:value="draftValue[field.key]"
              :placeholder="field.placeholder || field.environment"
              allow-clear
            />
            <small>
              {{ field.sensitive ? '留空并保存会清除数据库中的值。' : field.help }}
            </small>
          </label>
        </div>
        <div v-else class="option-fields option-values">
          <div v-for="field in option.params.fields" :key="field.key" class="option-field">
            <span class="setting-label">{{ field.label }}</span>
            <a-input
              :value="getFieldDisplay(option, field)"
              :class="{ 'masked-value': field.sensitive }"
              disabled
            />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { configOptionsApi } from '@/apis/system_api'
import { useConfigStore } from '@/stores/config'
import OCRSelector from '@/components/OCRSelector.vue'

const configStore = useConfigStore()
const OCR_OPTION_KEYS = new Set([
  'mineru_ocr_host_opts',
  'mineru_official_api_opts',
  'pp_structure_v3_ocr_host_opts',
  'paddleocr_api_opts'
])
const items = computed(() => configStore.config?._config_items || {})
const configOptions = ref([])
const editingKey = ref('')
const draftValue = ref({})
const savingOption = ref('')

const loadConfigOptions = async () => {
  try {
    const data = await configOptionsApi.getOptions()
    configOptions.value = (data.options || [])
      .filter((option) => OCR_OPTION_KEYS.has(option.key))
      .map((option) => ({
        ...option,
        value: { ...(option.value || {}) },
        sensitive_state: { ...(option.sensitive_state || {}) }
      }))
  } catch (error) {
    message.error(error.message || '加载 OCR 服务配置失败')
  }
}

const startEditing = (option) => {
  editingKey.value = option.key
  draftValue.value = { ...(option.value || {}) }
}

const cancelEditing = () => {
  editingKey.value = ''
  draftValue.value = {}
}

const getFieldDisplay = (option, field) => {
  if (!field.sensitive) {
    return option.value?.[field.key] || `读取 ${field.environment}`
  }
  const state = option.sensitive_state?.[field.key]
  if (state?.source === 'database') return state.preview
  if (state?.source === 'environment') return `已通过 ${field.environment} 配置`
  return '未配置'
}

const saveOption = async (option) => {
  savingOption.value = option.key
  try {
    const data = await configOptionsApi.updateOption(option.key, draftValue.value)
    Object.assign(option, data.option, {
      value: { ...(data.option.value || {}) },
      sensitive_state: { ...(data.option.sensitive_state || {}) }
    })
    cancelEditing()
    message.success('配置已保存')
  } catch (error) {
    message.error(error.message || '保存配置失败')
  } finally {
    savingOption.value = ''
  }
}

onMounted(loadConfigOptions)
</script>

<style lang="less" scoped>
.settings-panel,
.option-card {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.settings-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-title {
  margin: 0 0 10px;
  color: var(--gray-900);
  font-size: 15px;
  font-weight: 600;

  &:not(:first-child) {
    margin-top: 24px;
  }
}

.section-description {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.option-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

.option-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;

  h4,
  p {
    margin: 0;
  }

  h4 {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
  }

  p {
    margin-top: 3px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.option-actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.save-button {
  border-color: var(--gray-900);
  background: var(--gray-900);

  &:hover,
  &:focus {
    border-color: var(--gray-700);
    background: var(--gray-700);
  }
}

.option-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 16px;
}

.option-field {
  display: flex;
  flex-direction: column;
  gap: 6px;

  &:only-child {
    grid-column: 1 / -1;
  }

  small {
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 1.5;
  }
}

.setting-label {
  color: var(--gray-700);
  font-size: 13px;
  font-weight: 500;
}

.masked-value {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  letter-spacing: 0.02em;
}

@media (max-width: 680px) {
  .option-fields {
    grid-template-columns: 1fr;
  }

  .option-field:only-child {
    grid-column: auto;
  }
}
</style>
