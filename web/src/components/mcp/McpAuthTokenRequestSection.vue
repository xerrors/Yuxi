<template>
  <section class="auth-config-section">
    <div class="section-heading">
      <span>Token 获取接口</span>
      <small>适配公司 API 网关、IAM 或其他内部换 token 服务。</small>
    </div>
    <div class="auth-form-grid">
      <div class="auth-field-row span-2">
        <label>Token 接口 URL</label>
        <a-input
          v-model:value="url"
          :readonly="readonly"
          placeholder="例如：http://gateway.internal/api/token"
        />
      </div>
      <div class="auth-field-row">
        <label>请求方法</label>
        <a-select v-model:value="method" :disabled="readonly">
          <a-select-option value="POST">POST</a-select-option>
          <a-select-option value="GET">GET</a-select-option>
          <a-select-option value="PUT">PUT</a-select-option>
        </a-select>
      </div>
      <div class="auth-field-row">
        <label>Body 类型</label>
        <a-select v-model:value="bodyType" :disabled="readonly">
          <a-select-option value="json">JSON</a-select-option>
          <a-select-option value="form">Form</a-select-option>
        </a-select>
      </div>
    </div>

    <a-collapse ghost class="auth-inner-collapse">
      <a-collapse-panel key="request" header="请求参数">
        <div class="kv-section">
          <div class="kv-title">请求头</div>
          <McpAuthRowEditor
            v-model="headers"
            :readonly="readonly"
            first-placeholder="Header 名称"
            second-placeholder="Header 值"
            row-key-prefix="header"
          />
        </div>
        <div class="kv-section">
          <div class="kv-title">Body 模板</div>
          <McpAuthRowEditor
            v-model="bodyTemplate"
            :readonly="readonly"
            first-placeholder="字段名"
            second-placeholder="模板值，如 ${secret.client_id}"
            row-key-prefix="body"
          />
        </div>
      </a-collapse-panel>
      <a-collapse-panel key="response" header="响应映射">
        <div class="kv-section">
          <div class="kv-title">把网关响应映射为标准 token 字段</div>
          <McpAuthRowEditor
            v-model="responseMap"
            :readonly="readonly"
            first-placeholder="标准字段，如 access_token"
            second-placeholder="响应路径，如 data.access_token"
            row-key-prefix="response"
          />
        </div>
      </a-collapse-panel>
    </a-collapse>
  </section>
</template>

<script setup>
import McpAuthRowEditor from './McpAuthRowEditor.vue'

const url = defineModel('url', { type: String, default: '' })
const method = defineModel('method', { type: String, default: 'POST' })
const bodyType = defineModel('bodyType', { type: String, default: 'json' })
const headers = defineModel('headers', { type: Array, required: true })
const bodyTemplate = defineModel('bodyTemplate', { type: Array, required: true })
const responseMap = defineModel('responseMap', { type: Array, required: true })

defineProps({
  readonly: { type: Boolean, default: false }
})
</script>

<style lang="less" scoped>
.auth-config-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.section-heading {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;

  span {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
  }

  small {
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.5;
  }
}

.auth-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.auth-field-row {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;

  &.span-2 {
    grid-column: 1 / -1;
  }

  label {
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
  }
}

.auth-inner-collapse {
  border-radius: 8px;
  background: var(--gray-25);
}

.kv-section {
  display: flex;
  flex-direction: column;
  gap: 8px;

  & + .kv-section {
    margin-top: 14px;
  }
}

.kv-title {
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;
}

@media (max-width: 720px) {
  .auth-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
