<template>
  <div class="settings-page device-settings">
    <section class="settings-section-block">
      <header class="settings-header">
        <div>
          <h3 class="settings-section-title">我的设备</h3>
          <p class="settings-section-description">
            默认授权本人全部设备与能力，可在 aBridge 中调整范围。
          </p>
        </div>
        <div class="device-actions">
          <a-button :loading="loading" @click="loadDevices">刷新设备</a-button>
          <a-button
            v-if="managementUrl"
            :href="managementUrl"
            target="_blank"
            rel="noopener noreferrer"
            >进入 aBridge</a-button
          >
        </div>
      </header>
      <div :aria-busy="loading">
        <div
          v-if="loading && !data"
          class="device-loading"
          role="status"
          aria-label="正在加载设备"
        >
          <ul class="device-list device-skeleton" aria-hidden="true">
            <li v-for="row in 3" :key="row">
              <a-skeleton
                active
                :avatar="{ shape: 'square', size: 24 }"
                :title="{ width: '48%' }"
                :paragraph="{ rows: 1, width: '24%' }"
              />
              <span class="device-skeleton-status" />
            </li>
          </ul>
        </div>
        <div v-else-if="loadError" role="alert" class="device-error">
          {{ loadError }} <a-button size="small" @click="loadDevices">重试</a-button>
        </div>
        <template v-else-if="data">
          <a-alert
            v-if="data.state === 'not_configured'"
            type="info"
            message="尚未配置设备服务，请联系管理员。"
            show-icon
          />
          <a-alert
            v-else-if="data.state === 'login_required'"
            type="info"
            message="请先进入 aBridge，使用 Yuxi 账户登录并绑定设备，再刷新列表。"
            show-icon
          />
          <template v-else>
            <p v-if="!data.devices.length" class="settings-section-description">
              暂无授权给 Yuxi 的设备。请进入 aBridge 绑定设备或检查连接授权。
            </p>
            <ul v-else class="device-list" aria-label="我的设备">
              <li v-for="device in devices" :key="device.id">
                <div class="device-identity">
                  <img v-if="device.icon" :src="device.icon" class="device-icon" alt="" />
                  <Monitor v-else class="device-icon" aria-hidden="true" />
                  <div class="device-text">
                    <strong>{{ device.name }}</strong>
                    <span class="device-detail">{{ device.platformLabel }}</span>
                  </div>
                </div>
                <span
                  class="device-status"
                  :class="{
                    'is-online': !device.paused && device.status === 'online',
                    'is-paused': device.paused
                  }"
                >
                  <span class="device-status-dot" aria-hidden="true" />
                  {{ device.paused ? '已暂停' : device.status === 'online' ? '在线' : '离线' }}
                </span>
              </li>
            </ul>
          </template>
        </template>
      </div>
    </section>

    <section v-if="userStore.isSuperAdmin" class="settings-section-block">
      <h3 class="settings-section-title">实例配置</h3>
      <a-spin :spinning="configLoading">
        <div v-if="configError" role="alert" class="device-error">
          {{ configError }} <a-button size="small" @click="loadConfig">重试</a-button>
        </div>
        <div v-else-if="option" class="settings-service-list">
          <section class="settings-service-card">
            <header class="settings-service-header">
              <div class="settings-service-heading">
                <div class="settings-service-title">
                  <h4>{{ option.name }}</h4>
                  <span v-if="option.params.fields.length === 1" class="settings-service-field-name">
                    {{ option.params.fields[0].label }}
                  </span>
                </div>
                <p class="settings-service-description">{{ option.description }}</p>
              </div>
              <div class="settings-service-actions" :class="{ editing }">
                <template v-if="editing">
                  <a-button size="small" :disabled="saving" @click="cancelEditing">取消</a-button>
                  <a-button type="primary" size="small" :loading="saving" @click="saveConfig"
                    >保存</a-button
                  >
                </template>
                <a-button v-else type="text" size="small" @click="startEditing">编辑</a-button>
              </div>
            </header>
            <div class="settings-service-fields">
              <div
                v-for="field in option.params.fields"
                :key="field.key"
                class="settings-service-field"
              >
                <label
                  v-if="option.params.fields.length > 1"
                  :for="`abridge-${field.key}`"
                  class="settings-form-label"
                >{{ field.label }}</label>
                <a-input
                  v-if="editing"
                  :id="`abridge-${field.key}`"
                  v-model:value="draftValue[field.key]"
                  :aria-label="field.label"
                  :placeholder="field.environment"
                  :disabled="saving"
                  allow-clear
                />
                <a-input
                  v-else
                  :id="`abridge-${field.key}`"
                  :aria-label="field.label"
                  :value="option.value?.[field.key] || field.default || `读取 ${field.environment}`"
                  disabled
                />
                <small v-if="field.help">{{ field.help }}</small>
              </div>
            </div>
          </section>
        </div>
      </a-spin>
    </section>
    <footer class="device-credit">
      <span>powered by</span>
      <a href="https://github.com/xerrors/aBridge" target="_blank" rel="noopener noreferrer">
        aBridge
      </a>
    </footer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { Monitor } from '@lucide/vue'
import { deviceAppearance } from '@/shared/lib/deviceAppearance'
import { devicesApi } from '@/apis/devices_api'
import { configOptionsApi } from '@/apis/system_api'
import { useUserStore } from '@/modules/identity'

const userStore = useUserStore()
const data = ref(null)
const devices = computed(() =>
  (data.value?.devices || []).map((device) => ({ ...device, ...deviceAppearance(device) }))
)
const loading = ref(false)
const loadError = ref('')
let devicesRequest = 0
const option = ref(null)
const configLoading = ref(false)
const configError = ref('')
const editing = ref(false)
const draftValue = ref({})
const saving = ref(false)
const managementUrl = computed(() => {
  try {
    const url = new URL(data.value?.management_url)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : ''
  } catch {
    return ''
  }
})

/** 刷新时保留已读设备，失败时清除旧列表，避免将旧状态当作最新结果。 */
async function loadDevices() {
  const request = ++devicesRequest
  loading.value = true
  loadError.value = ''
  try {
    const result = await devicesApi.list()
    if (request === devicesRequest) data.value = result
  } catch (error) {
    if (request === devicesRequest) {
      data.value = null
      loadError.value = error.message || '读取设备失败，请重试'
    }
  } finally {
    if (request === devicesRequest) loading.value = false
  }
}

/** 只对超级管理员读取实例配置。 */
async function loadConfig() {
  configLoading.value = true
  configError.value = ''
  try {
    const result = await configOptionsApi.getOptions()
    option.value = result.options.find((item) => item.key === 'abridge_options')
    if (!option.value) throw new Error('未找到设备服务配置，请联系管理员')
  } catch (error) {
    configError.value = error.message || '读取配置失败'
  } finally {
    configLoading.value = false
  }
}

/** 编辑数据库覆盖值；地址留空时继续读取环境变量。 */
function startEditing() {
  editing.value = true
  draftValue.value = Object.fromEntries(
    option.value.params.fields.map((field) => [
      field.key,
      option.value.value?.[field.key] ?? field.default ?? ''
    ])
  )
}

/** 取消编辑并丢弃尚未提交的地址。 */
function cancelEditing() {
  editing.value = false
  draftValue.value = {}
}

/** 保存地址覆盖值，留空时继续读取环境变量。 */
async function saveConfig() {
  saving.value = true
  try {
    const fields = Object.fromEntries(
      option.value.params.fields.map((field) => [field.key, draftValue.value[field.key]?.trim() || ''])
    )
    const result = await configOptionsApi.updateOption('abridge_options', fields)
    option.value = result.option
    cancelEditing()
    message.success('设备服务配置已保存')
    await loadDevices()
  } catch (error) {
    message.error(error.message || '保存设备服务配置失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadDevices()
  if (userStore.isSuperAdmin) loadConfig()
})
</script>

<style scoped lang="less">
.device-settings {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}
.device-credit {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: auto;
  padding-top: 16px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 20px;

  a {
    border-radius: 3px;
    color: var(--color-text-secondary);
    font-weight: 600;
    text-decoration: none;

    &:hover {
      color: var(--main-color);
      text-decoration: underline;
      text-underline-offset: 3px;
    }

    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: 3px;
    }
  }
}
.device-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.device-error {
  color: var(--color-error-700);
  overflow-wrap: anywhere;
}
.device-list {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
  border: 1px solid var(--border-surface);
  border-radius: 8px;
}
.device-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  color: var(--color-text);
  overflow-wrap: anywhere;
}
.device-list li + li {
  border-top: 1px solid var(--border-surface);
}
.device-list li > :first-child {
  min-width: 0;
}
.device-list li > :last-child {
  flex-shrink: 0;
}
.device-identity {
  display: flex;
  align-items: center;
  gap: 12px;
}
.device-icon {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  object-fit: contain;
}
.device-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 4px;
}
.device-detail {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.device-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  white-space: nowrap;

  &.is-online {
    background: var(--color-success-50);
    color: var(--color-success-900);
  }

  &.is-paused {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }
}
.device-status-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: currentColor;
}
.device-skeleton li {
  min-height: 69px;
}
.device-skeleton :deep(.ant-skeleton-header) {
  vertical-align: middle;
  padding-right: 12px;
}
.device-skeleton :deep(.ant-skeleton-title) {
  height: 14px;
  margin: 0 0 8px;
}
.device-skeleton :deep(.ant-skeleton-content .ant-skeleton-title + .ant-skeleton-paragraph) {
  margin: 0;
}
.device-skeleton :deep(.ant-skeleton-paragraph li) {
  height: 12px;
  min-height: 0;
  padding: 0;
}
.device-skeleton-status {
  width: 28px;
  height: 12px;
  border-radius: 4px;
  background: var(--gray-100);
}
@media (prefers-reduced-motion: reduce) {
  .device-skeleton :deep(.ant-skeleton-content *::after),
  .device-skeleton :deep(.ant-skeleton-avatar::after) {
    animation: none;
  }
}
</style>
