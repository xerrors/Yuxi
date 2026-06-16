<template>
  <div class="auth-json-mode">
    <div class="json-guide-grid">
      <div>
        <strong>常用字段</strong>
        <p><code>provider</code> 决定鉴权方式，<code>binding_scope</code> 决定连接隔离。</p>
      </div>
      <div>
        <strong>接口换 Token</strong>
        <p><code>token_request</code> 描述 URL、请求头、Body 模板和响应映射。</p>
      </div>
      <div>
        <strong>注入规则</strong>
        <p><code>inject.entries</code> 决定最终写入 MCP 请求头或环境变量。</p>
      </div>
    </div>
    <a-textarea
      v-model:value="draft"
      :rows="12"
      class="auth-json-textarea"
      :readonly="readonly"
      placeholder="粘贴 auth_config JSON；留空表示不启用动态鉴权"
    />
    <div v-if="!readonly" class="json-action-row">
      <a-button size="small" @click="$emit('format')">格式化</a-button>
      <a-button size="small" type="primary" @click="$emit('import')">导入到向导</a-button>
    </div>
    <a-alert
      v-if="jsonError"
      class="auth-warning"
      type="warning"
      show-icon
      :message="jsonError"
    />
  </div>
</template>

<script setup>
const draft = defineModel({ type: String, default: '' })

defineProps({
  readonly: { type: Boolean, default: false },
  jsonError: { type: String, default: '' }
})

defineEmits(['format', 'import'])
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.auth-json-mode {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.json-guide-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;

  > div {
    padding: 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
  }

  strong {
    color: var(--gray-900);
    font-size: 13px;
  }

  p {
    margin: 6px 0 0;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.5;
  }

  code {
    font-family: @mono-font;
  }
}

.auth-json-textarea {
  font-family: @mono-font;
  font-size: 13px;
  line-height: 1.6;
}

.json-action-row {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.auth-warning {
  margin-top: 0;
}

@media (max-width: 720px) {
  .json-guide-grid {
    grid-template-columns: 1fr;
  }
}
</style>
