<template>
  <div class="web-settings settings-page">
    <a-spin :spinning="loading">
      <section class="settings-section-block">
        <h3 class="settings-section-title">默认搜索服务</h3>
        <div v-if="loadError" role="alert" class="web-error">
          {{ loadError }} <a-button size="small" @click="load">重试</a-button>
        </div>
        <section v-else class="settings-form-panel" aria-label="默认网页工具供应商">
          <div v-for="operation in ['search', 'fetch']" :key="operation" class="settings-form-row">
            <div class="settings-form-copy">
              <span class="settings-form-label">{{
                operation === 'search' ? '搜索服务' : '网页抓取服务'
              }}</span>
              <p class="settings-form-description">
                {{
                  operation === 'search'
                    ? '联网搜索时，使用此服务查找网页信息。'
                    : '读取网页时，使用此服务提取正文内容。'
                }}
              </p>
            </div>
            <div class="settings-form-control is-compact">
              <a-select
                :aria-label="operation === 'search' ? '搜索服务' : '网页抓取服务'"
                :value="defaultProvider(operation, config, providers, options)"
                :options="providerChoices(operation)"
                placeholder="请先配置 API Key"
                :loading="savingDefault === operation"
                :disabled="Boolean(savingDefault) || loading"
                @change="saveDefault(operation, $event)"
              />
            </div>
          </div>
        </section>
      </section>

      <section v-if="!loadError" class="settings-section-block">
        <h3 class="settings-section-title">服务配置</h3>
        <div class="settings-service-list">
          <section
            v-for="provider in providers"
            :key="provider.id"
            class="settings-service-card"
            :aria-label="provider.name + ' 配置'"
          >
            <header class="settings-service-header">
              <div class="settings-service-heading">
                <div class="settings-service-title">
                  <h4>{{ provider.name }}</h4>
                  <span class="settings-service-field-name">API Key</span>
                  <div
                    v-if="results[provider.id]"
                    class="test-summary"
                    role="status"
                    aria-live="polite"
                  >
                    <span
                      v-for="(result, operation) in results[provider.id]"
                      :key="operation"
                      :class="['test-state', result.state]"
                    >
                      {{ operation === 'search' ? '搜索' : 'Fetch'
                      }}{{ { running: '测试中…', passed: '通过', failed: '失败' }[result.state]
                      }}<template v-if="result.error">：{{ result.error }}</template>
                    </span>
                  </div>
                </div>
                <p v-if="provider.id === 'tavily'" class="settings-service-description">
                  每月免费额度 ·
                  <a
                    href="https://docs.tavily.com/documentation/api-credits"
                    target="_blank"
                    rel="noopener noreferrer"
                    >官方计费</a
                  >
                </p>
                <p v-else class="settings-service-description">
                  {{ provider.fetch ? '搜索与网页抓取' : '网页搜索' }}
                </p>
              </div>
              <div
                class="settings-service-actions"
                :class="{ editing: editingKey === provider.id || testing[provider.id] }"
              >
                <a-button
                  type="text"
                  size="small"
                  :loading="testing[provider.id]"
                  :disabled="testDisabled(provider)"
                  @click="runTest(provider)"
                  >测试</a-button
                >
                <template v-if="editingKey === provider.id">
                  <a-button
                    size="small"
                    :disabled="Boolean(savingKey) || testing[provider.id]"
                    @click="cancelEditing"
                    >取消</a-button
                  >
                  <a-button
                    type="primary"
                    size="small"
                    :loading="savingKey === provider.id"
                    :disabled="testing[provider.id]"
                    @click="saveKey(provider)"
                    >保存</a-button
                  >
                </template>
                <a-button
                  v-else
                  type="text"
                  size="small"
                  :disabled="Boolean(editingKey) || testing[provider.id] || loading"
                  @click="startEditing(provider)"
                  >编辑</a-button
                >
              </div>
            </header>
            <div v-if="editingKey === provider.id" class="settings-service-field">
              <a-input-password
                aria-label="API Key"
                v-model:value="draftKey"
                :placeholder="provider.environment"
                :disabled="clearKey || Boolean(savingKey) || testing[provider.id]"
                autocomplete="new-password"
                @update:value="delete results[provider.id]"
              />
              <small>留空保持现有凭据；勾选清除会移除数据库中的值，并沿用环境变量（如已配置）。</small>
              <a-checkbox v-model:checked="clearKey" :disabled="Boolean(savingKey) || testing[provider.id]">
                清除已保存的 API Key
              </a-checkbox>
            </div>
            <div v-else class="settings-service-field">
              <a-input
                aria-label="API Key"
                :value="credentialDescription(provider)"
                class="settings-masked-value"
                disabled
              />
            </div>
          </section>
        </div>
      </section>
    </a-spin>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { configApi, configOptionsApi, webToolsApi } from '@/apis/system_api'
import { defaultProvider, keyState, testOutcome, testPayload, webProviders } from '../model/webSearchSettings'

const options = reactive({})
const providers = ref([])
const config = ref({})
const editingKey = ref('')
const draftKey = ref('')
const clearKey = ref(false)
const savingKey = ref('')
const testing = reactive({})
const results = reactive({})
const loading = ref(true)
const loadError = ref('')
const savingDefault = ref('')

const load = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const [optionData, configData] = await Promise.all([configOptionsApi.getOptions(), configApi.getConfig()])
    const providerOptions = optionData.options.filter(({ params }) => params?.provider?.id)
    providers.value = webProviders(providerOptions)
    for (const option of providerOptions) {
      options[option.params.provider.id] = option
    }
    config.value = configData
  } catch (error) {
    loadError.value = error.message || '加载网络搜索配置失败'
  } finally {
    loading.value = false
  }
}

const providerChoices = (operation) => providers.value
  .filter((provider) => operation === 'search' || provider.fetch)
  .map((provider) => ({
    value: provider.id,
    label: provider.name,
    disabled: !keyState(options[provider.id]).configured
  }))

const credentialDescription = (provider) => {
  const state = keyState(options[provider.id])
  if (state.source === 'database') return state.preview
  if (state.source === 'environment') return `已通过 ${provider.environment} 配置`
  return '未配置'
}

const startEditing = (provider) => {
  editingKey.value = provider.id
  draftKey.value = ''
  clearKey.value = false
  delete results[provider.id]
}

const cancelEditing = () => {
  delete results[editingKey.value]
  editingKey.value = ''
  draftKey.value = ''
  clearKey.value = false
}

const saveKey = async (provider) => {
  if (!clearKey.value && !draftKey.value.trim()) {
    cancelEditing()
    message.info('已保留现有凭据')
    return
  }
  savingKey.value = provider.id
  try {
    const data = await configOptionsApi.updateOption(provider.optionKey, { api_key: clearKey.value ? '' : draftKey.value.trim() })
    options[provider.id] = data.option
    delete results[provider.id]
    cancelEditing()
    message.success('配置已保存')
  } catch (error) {
    message.error(error.message || '保存配置失败')
  } finally {
    savingKey.value = ''
  }
}

const saveDefault = async (operation, provider) => {
  savingDefault.value = operation
  try {
    config.value = await configApi.updateConfig(`web_${operation}_provider`, provider)
    message.success('默认供应商已保存')
  } catch (error) {
    message.error(error.message || '保存默认供应商失败')
  } finally {
    savingDefault.value = ''
  }
}

const testDisabled = (provider) => {
  const hasKey = editingKey.value === provider.id
    ? !clearKey.value && Boolean(draftKey.value.trim())
    : keyState(options[provider.id]).configured
  return Boolean(loading.value || savingKey.value || testing[provider.id] || !hasKey)
}

const runTest = async (provider) => {
  if (testDisabled(provider)) return
  testing[provider.id] = true
  const draft = editingKey.value === provider.id ? draftKey.value : ''
  const operations = provider.fetch ? ['search', 'fetch'] : ['search']
  results[provider.id] = Object.fromEntries(operations.map((operation) => [operation, { state: 'running' }]))
  await Promise.all(operations.map(async (operation) => {
    try {
      const response = await webToolsApi[operation](testPayload(provider.id, operation, draft))
      results[provider.id][operation] = testOutcome(operation, response)
    } catch (error) {
      results[provider.id][operation] = { state: 'failed', error: error.message || '测试失败' }
    }
  }))
  testing[provider.id] = false
}

onMounted(load)
</script>

<style scoped lang="less">
.test-summary { display: flex; flex-wrap: wrap; gap: 4px 12px; min-width: 0; font-size: 12px; overflow-wrap: anywhere; }
.test-state { color: var(--color-text-secondary); }
.passed { color: var(--color-success-700); }
.failed, .web-error { color: var(--color-error-700); }
</style>
