<template>
  <div class="auth-preview-panel">
    <div>
      <span class="preview-label">连接页需要填写</span>
      <div v-if="secretFields.length > 0" class="secret-chip-row">
        <span v-for="field in secretFields" :key="field" class="secret-chip">
          {{ getSecretFieldLabel(field) }}
        </span>
      </div>
      <p v-else>
        当前配置未引用 <code>${secret.xxx}</code>，由于无需长期凭据，您可以直接进行测试，无需强制绑定连接。
      </p>
    </div>
    <div>
      <span class="preview-label">可用模板变量</span>
      <p>
        <code>${access_token}</code>、<code>${secret.client_id}</code>、<code>${context.user_id}</code>、<code>${context.work_id}</code>、<code>${context.department_id}</code>
      </p>
    </div>
  </div>
</template>

<script setup>
import { getMcpSecretFieldLabel } from '@/utils/mcpConnectionUtils'

defineProps({
  secretFields: { type: Array, default: () => [] }
})

const getSecretFieldLabel = getMcpSecretFieldLabel
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.auth-preview-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  gap: 12px;
  padding: 12px 14px;
  border: 1px dashed var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);

  p {
    margin: 6px 0 0;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.6;
  }

  code {
    font-family: @mono-font;
  }
}

.preview-label {
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;
}

.secret-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.secret-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--main-10);
  color: var(--main-color);
  font-size: 12px;
  font-weight: 500;
}

@media (max-width: 720px) {
  .auth-preview-panel {
    grid-template-columns: 1fr;
  }
}
</style>
