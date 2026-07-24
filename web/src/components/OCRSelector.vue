<template>
  <a-dropdown
    trigger="click"
    :open="dropdownOpen"
    :disabled="disabled"
    @open-change="handleOpenChange"
  >
    <button
      type="button"
      class="ocr-selector-trigger"
      :class="{ disabled }"
      :disabled="disabled"
      :aria-expanded="dropdownOpen"
      @click.prevent.stop
    >
      <span class="ocr-selector-trigger-text">{{ selectedEngineLabel }}</span>
      <RefreshCw v-if="healthLoading" :size="13" class="spin" aria-label="正在检测 OCR 状态" />
      <ChevronDown v-else :size="14" />
    </button>

    <template #overlay>
      <div class="ocr-selector-dropdown" @click.stop>
        <div class="ocr-selector-header">
          <span>OCR 方法</span>
          <button v-if="userStore.isAdmin" type="button" class="config-link" @click="goToConfig">
            去配置
          </button>
        </div>
        <div class="ocr-selector-options">
          <div v-if="optionsLoading" class="ocr-selector-empty">加载中...</div>
          <div v-else-if="visibleEngines.length === 0" class="ocr-selector-empty">
            暂无可用 OCR 方法
          </div>
          <template v-else>
            <button
              v-for="engine in activeEngines"
              :key="engine.engine_id"
              type="button"
              class="ocr-selector-option"
              :class="{ selected: modelValue === engine.engine_id }"
              @click="selectEngine(engine.engine_id)"
            >
              <span class="ocr-option-main">
                <span class="ocr-option-name">{{ engine.display_name }}</span>
                <span :class="['ocr-health-label', healthClass(engine.engine_id)]">
                  {{ healthLabel(engine.engine_id) }}
                </span>
              </span>
            </button>
            <button
              v-if="unavailableEngines.length"
              type="button"
              class="unavailable-toggle"
              :aria-expanded="unavailableExpanded"
              @click="unavailableExpanded = !unavailableExpanded"
            >
              <span>不可用（{{ unavailableEngines.length }}）</span>
              <ChevronDown v-if="unavailableExpanded" :size="13" />
              <ChevronRight v-else :size="13" />
            </button>
            <template v-if="unavailableExpanded">
              <button
                v-for="engine in unavailableEngines"
                :key="engine.engine_id"
                type="button"
                class="ocr-selector-option unavailable"
                :class="{ selected: modelValue === engine.engine_id }"
                disabled
              >
                <span class="ocr-option-main">
                  <span class="ocr-option-name">{{ engine.display_name }}</span>
                  <span class="ocr-health-label unhealthy">不可用</span>
                </span>
                <span class="ocr-unavailable-reason">
                  {{ health[engine.engine_id]?.message || '健康检测未通过' }}
                </span>
              </button>
            </template>
          </template>
        </div>
      </div>
    </template>
  </a-dropdown>
</template>

<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-vue-next'
import { ocrApi } from '@/apis/system_api'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  modelValue: { type: String, default: '' },
  allowedEngines: { type: Array, default: () => [] },
  includeDisable: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '请选择 OCR 方法' }
})

const emit = defineEmits(['update:modelValue', 'change', 'options-loaded'])

const userStore = useUserStore()
const { openSettingsModal } = inject('settingsModal', {})
const engines = ref([])
const health = ref({})
const dropdownOpen = ref(false)
const optionsLoading = ref(false)
const healthLoading = ref(false)
const unavailableExpanded = ref(false)

const allowedEngineSet = computed(() => new Set(props.allowedEngines || []))
const selectableEngines = computed(() => {
  if (!props.includeDisable || engines.value.some((engine) => engine.engine_id === 'disable')) {
    return engines.value
  }
  return [{ engine_id: 'disable', display_name: '禁用 OCR' }, ...engines.value]
})
const visibleEngines = computed(() =>
  selectableEngines.value.filter((engine) => {
    if (!props.includeDisable && engine.engine_id === 'disable') return false
    return allowedEngineSet.value.size === 0 || allowedEngineSet.value.has(engine.engine_id)
  })
)

const selectedEngineLabel = computed(() => {
  if (props.modelValue === 'disable') return '已禁用 OCR'
  return (
    visibleEngines.value.find((engine) => engine.engine_id === props.modelValue)?.display_name ||
    props.placeholder
  )
})
const unavailableEngines = computed(() =>
  visibleEngines.value.filter(
    (engine) => healthStatus(engine.engine_id) && !isHealthy(engine.engine_id)
  )
)
const unavailableEngineIds = computed(
  () => new Set(unavailableEngines.value.map((engine) => engine.engine_id))
)
const activeEngines = computed(() =>
  visibleEngines.value.filter((engine) => !unavailableEngineIds.value.has(engine.engine_id))
)

const loadOptions = async () => {
  optionsLoading.value = true
  try {
    const data = await ocrApi.getOptions()
    engines.value = Array.isArray(data?.engines) ? data.engines : []
    emit('options-loaded', data)
  } catch (error) {
    console.error('获取 OCR 方法失败:', error)
  } finally {
    optionsLoading.value = false
  }
}

const refreshHealth = async () => {
  healthLoading.value = true
  try {
    const data = await ocrApi.getHealth()
    health.value = data?.health || {}
  } catch (error) {
    console.error('获取 OCR 健康状态失败:', error)
  } finally {
    healthLoading.value = false
  }
}

const handleOpenChange = (open) => {
  if (props.disabled) {
    dropdownOpen.value = false
    return
  }
  dropdownOpen.value = open
  if (open) {
    unavailableExpanded.value = false
    void Promise.allSettled([loadOptions(), refreshHealth()])
  }
}

const selectEngine = (engineId) => {
  emit('update:modelValue', engineId)
  emit('change', engineId)
  dropdownOpen.value = false
}

const goToConfig = () => {
  dropdownOpen.value = false
  openSettingsModal?.('ocr')
}

const healthStatus = (engineId) => health.value[engineId]?.status || ''
const isHealthy = (engineId) => ['healthy', 'configured', 'ok'].includes(healthStatus(engineId))

const healthClass = (engineId) => {
  if (engineId === 'disable') return 'healthy'
  if (healthLoading.value && !healthStatus(engineId)) return 'checking'
  if (!healthStatus(engineId)) return 'unknown'
  return isHealthy(engineId) ? 'healthy' : 'unhealthy'
}

const healthLabel = (engineId) => {
  if (engineId === 'disable') return '无需检测'
  if (healthLoading.value && !healthStatus(engineId)) return '检测中'
  if (!healthStatus(engineId)) return '未检测'
  return isHealthy(engineId) ? '可用' : '不可用'
}

onMounted(() => {
  void loadOptions()
})
</script>

<style lang="less" scoped>
.ocr-selector-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  min-height: 32px;
  padding: 4px 11px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
  color: var(--gray-900);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition: border-color 0.2s ease;

  &:hover:not(.disabled),
  &:focus-visible {
    border-color: var(--main-color);
    outline: none;
  }

  &.disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
}

.ocr-selector-trigger-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ocr-selector-dropdown {
  width: min(360px, calc(100vw - 24px));
  overflow: hidden;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
}

.ocr-selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--gray-100);
  color: var(--gray-500);
  background: var(--gray-25);
  font-size: 11px;
}

.config-link {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--main-color);
  cursor: pointer;
  font-size: 11px;

  &:hover,
  &:focus-visible {
    text-decoration: underline;
    outline: none;
  }
}

.ocr-selector-options {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  padding: 6px;
  overflow-y: auto;
}

.ocr-selector-option {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition: background 0.2s ease;

  &:hover,
  &:focus-visible {
    background: var(--gray-50);
    outline: none;
  }

  &.selected {
    background: var(--main-50);
  }

  &.unavailable {
    opacity: 0.72;
  }
}

.ocr-option-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.ocr-option-name {
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 500;
}

.ocr-health-label {
  flex: none;
  font-size: 11px;

  &.healthy {
    color: var(--color-success-700);
  }

  &.unhealthy {
    color: var(--color-error-700);
  }

  &.checking,
  &.unknown {
    color: var(--gray-500);
  }
}

.ocr-selector-empty {
  color: var(--gray-500);
  font-size: 11px;
  line-height: 1.45;
}

.ocr-unavailable-reason {
  color: var(--gray-500);
  font-size: 11px;
  line-height: 1.45;
}

.unavailable-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-top: 1px solid var(--gray-100);
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  font-size: 11px;
  text-align: left;

  &:hover,
  &:focus-visible {
    color: var(--gray-700);
    outline: none;
  }
}

.ocr-selector-empty {
  padding: 20px 12px;
  text-align: center;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ocr-selector-trigger,
  .ocr-selector-option {
    transition: none;
  }

  .spin {
    animation: none;
  }
}
</style>
