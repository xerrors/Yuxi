<template>
  <a-dropdown trigger="click" @open-change="handleOpenChange">
    <div class="model-select" @click.prevent>
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
            {{ model.display_name }}
          </a-menu-item>
        </a-menu-item-group>
      </a-menu>
    </template>
  </a-dropdown>
</template>

<script setup>
import { computed, ref } from 'vue'
import { modelProviderApi } from '@/apis/system_api'

const props = defineProps({
  value: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '请选择重排序模型'
  },
  size: {
    type: String,
    default: 'small',
    validator: (value) => ['small', 'middle', 'large'].includes(value)
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
const displayText = computed(() => props.value || props.placeholder)

const handleOpenChange = async (open) => {
  if (!open) return
  try {
    const response = await modelProviderApi.getV2Models('rerank')
    if (response.success) {
      v2Models.value = response.data || {}
    }
  } catch (error) {
    console.error('获取 rerank 模型失败:', error)
  }
}

const handleSelect = (spec) => {
  emit('update:value', spec)
  emit('change', spec)
}
</script>

<style lang="less" scoped>
@import '@/assets/css/model-selector-common.less';
</style>
