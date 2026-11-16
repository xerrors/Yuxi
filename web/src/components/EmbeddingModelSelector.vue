<template>
  <a-dropdown trigger="click" @open-change="handleOpenChange">
    <div class="model-select" :class="modelSelectClasses" @click.prevent>
      <div class="model-select-content">
        <div class="model-info">
          <a-tooltip :title="displayText" placement="right">
            <span class="model-text">{{ displayText }}</span>
          </a-tooltip>
        </div>
      </div>
    </div>

    <template #overlay>
      <a-menu class="scrollable-menu">
        <a-menu-item-group v-for="(providerData, providerId) in v2Models" :key="providerId">
          <template #title>
            <span>{{ providerId }}</span>
          </template>
          <a-menu-item
            v-for="model in providerData.models"
            :key="model.spec"
            @click="handleSelect(model.spec)"
          >
            <div class="model-option">
              <span class="model-option-name">
                {{ model.display_name }}
                <span v-if="model.dimension" class="model-dimension">({{ model.dimension }})</span>
              </span>
              <span
                class="model-status-icon"
                :class="getStatusClass(model.spec)"
                :title="getStatusTooltip(model.spec)"
                >{{ getStatusIcon(model.spec) }}</span
              >
            </div>
          </a-menu-item>
        </a-menu-item-group>
      </a-menu>
    </template>
  </a-dropdown>
</template>

<script setup>
import { computed, ref } from 'vue'
import { modelProviderApi } from '@/apis/system_api'
import { useModelStatus } from '@/composables/useModelStatus'

const props = defineProps({
  value: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '请选择嵌入模型'
  },
  size: {
    type: String,
    default: 'small',
    validator: (value) => ['default', 'small', 'middle', 'large'].includes(value)
  },
  style: {
    type: Object,
    default: () => ({ width: '100%' })
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:value', 'change'])

const v2Models = ref({})
const { getStatusIcon, getStatusClass, getStatusTooltip, checkV2Statuses } = useModelStatus()

const displayText = computed(() => props.value || props.placeholder)
const resolvedSize = computed(() => props.size || 'small')
const modelSelectClasses = computed(() => ({
  'model-select--middle': resolvedSize.value === 'middle',
  'model-select--large': resolvedSize.value === 'large'
}))

const handleOpenChange = async (open) => {
  if (!open) return
  await fetchV2Models()
}

const fetchV2Models = async () => {
  try {
    const response = await modelProviderApi.getV2Models('embedding')
    if (response.success) {
      v2Models.value = response.data || {}
      await checkV2ModelStatuses()
    }
  } catch (error) {
    console.error('获取 embedding 模型失败:', error)
  }
}

const checkV2ModelStatuses = async () => {
  try {
    const models = Object.values(v2Models.value).flatMap(
      (providerData) => providerData.models || []
    )
    await checkV2Statuses(models)
  } catch (error) {
    console.error('检查 embedding 模型状态失败:', error)
  }
}

const handleSelect = (value) => {
  emit('update:value', value)
  emit('change', value)
}
</script>

<style lang="less" scoped>
@import '@/assets/css/model-selector-common.less';

.model-option {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  width: 100%;
}

.model-option-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-dimension {
  color: var(--gray-500);
  font-size: 12px;
  margin-left: 4px;
}

.model-status-icon {
  font-size: 11px;
  font-weight: bold;
  flex-shrink: 0;
  color: var(--gray-500);

  &.available {
    color: var(--color-success-500);
  }

  &.unavailable {
    color: var(--color-error-500);
  }

  &.error {
    color: var(--color-warning-500);
  }
}
</style>
