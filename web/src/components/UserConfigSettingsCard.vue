<template>
  <div class="user-config-settings">
    <a-spin :spinning="loading">
      <div class="config-panel">
        <div class="config-row">
          <div class="config-meta">
            <div class="config-title-line">
              <span class="config-title">是否启用 Memory</span>
              <span class="reserved-badge">预留开关</span>
            </div>
            <p class="config-description">当前仅保存配置值，暂不接入智能体运行逻辑。</p>
          </div>
          <a-switch :checked="draftEnableMemory" @change="handleMemoryChange" />
        </div>
      </div>
    </a-spin>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { userConfigApi } from '@/apis/user_config_api'

const loading = ref(false)
const saving = ref(false)
const draftEnableMemory = ref(false)
const savedEnableMemory = ref(false)

const applyResponse = (res) => {
  draftEnableMemory.value = res.enable_memory
  savedEnableMemory.value = res.enable_memory
}

const loadUserConfig = async () => {
  loading.value = true
  try {
    const res = await userConfigApi.get()
    applyResponse(res)
  } catch (error) {
    message.error(error.message || '加载用户配置失败')
  } finally {
    loading.value = false
  }
}

const handleMemoryChange = (val) => {
  draftEnableMemory.value = Boolean(val)
  saveUserConfig()
}

const saveUserConfig = async () => {
  saving.value = true
  try {
    const res = await userConfigApi.update({ enable_memory: draftEnableMemory.value })
    applyResponse(res)
    message.success('用户配置已保存')
  } catch (error) {
    message.error(error.message || '保存用户配置失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadUserConfig)

defineExpose({ refresh: loadUserConfig })
</script>

<style lang="less" scoped>
.user-config-settings {
  .config-panel {
    border-top: 1px solid var(--gray-150);
  }

  .config-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    padding: 16px 0 0;

    @media (max-width: 560px) {
      align-items: flex-start;
      flex-direction: column;
    }
  }

  .config-meta {
    min-width: 0;
  }

  .config-title-line {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .config-title {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
    line-height: 1.4;
  }

  .reserved-badge {
    display: inline-flex;
    align-items: center;
    height: 22px;
    padding: 0 8px;
    border-radius: 999px;
    border: 1px solid var(--color-warning-100);
    background: var(--color-warning-10);
    color: var(--color-warning-700);
    font-size: 12px;
    line-height: 1;
    white-space: nowrap;
  }

  .config-description {
    margin: 6px 0 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.5;
  }
}
</style>
