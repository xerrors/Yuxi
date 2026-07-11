<template>
  <div class="user-config-settings">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">用户配置(Beta)</div>
        <p class="section-description">
          配置当前用户的专属设置。Memory 用于在不同对话之间保留长期有效的偏好和经验。
        </p>
      </div>
      <div class="header-actions">
        <a-button class="lucide-icon-btn" :loading="loading" @click="loadUserConfig">
          <template #icon><RefreshCw :size="16" :class="{ spin: loading }" /></template>
          刷新
        </a-button>
        <a-button type="primary" :loading="saving" @click="saveUserConfig">
          {{ saveButtonText }}
        </a-button>
      </div>
    </div>

    <a-spin :spinning="loading">
      <div class="config-panel">
        <div class="config-row">
          <div class="config-meta">
            <div class="config-title-line">
              <span class="config-title">是否启用 Memory</span>
              <span class="reserved-badge">Beta</span>
            </div>
            <p class="config-description">
              启用后，主智能体通过专用工具读写用户工作区的 memory/MEMORY.md；子智能体暂不写入。
            </p>
          </div>
          <a-switch :checked="draftEnableMemory" @change="draftEnableMemory = Boolean($event)" />
        </div>
      </div>
    </a-spin>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import { userConfigApi } from '@/apis/user_config_api'

const loading = ref(false)
const saving = ref(false)
const draftEnableMemory = ref(false)
const savedEnableMemory = ref(false)

const hasUnsavedChanges = computed(() => draftEnableMemory.value !== savedEnableMemory.value)
const saveButtonText = computed(() => (hasUnsavedChanges.value ? '保存（有修改）' : '保存'))

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

const saveUserConfig = async () => {
  if (!hasUnsavedChanges.value) {
    message.info('用户配置未变化')
    return
  }

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
</script>

<style lang="less" scoped>
.user-config-settings {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 12px;

    @media (max-width: 760px) {
      align-items: stretch;
      flex-direction: column;
    }
  }

  .header-content {
    flex: 1;
    min-width: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .config-panel {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    overflow: hidden;
  }

  .config-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    padding: 14px 16px;

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

:deep(.spin) {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>
