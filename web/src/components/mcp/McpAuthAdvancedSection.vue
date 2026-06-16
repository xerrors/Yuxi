<template>
  <a-collapse ghost class="auth-advanced-collapse">
    <a-collapse-panel key="advanced" header="高级设置">
      <div class="auth-form-grid">
        <div class="auth-field-row">
          <label>MCP 清单隔离</label>
          <a-select v-model:value="manifestScope" :disabled="readonly">
            <a-select-option value="binding">按连接隔离</a-select-option>
            <a-select-option value="server">服务级共享</a-select-option>
          </a-select>
        </div>
        <div class="auth-field-row">
          <label>提前刷新秒数</label>
          <a-input-number
            v-model:value="preRefreshSeconds"
            :disabled="readonly"
            :min="0"
            :max="86400"
            style="width: 100%"
          />
        </div>
        <div class="auth-field-row span-2 compact">
          <label>401 处理</label>
          <a-switch v-model:checked="retryOnceOn401" :disabled="readonly" />
          <span class="field-helper">收到 401 时清理缓存并自动重试一次。</span>
        </div>
      </div>
    </a-collapse-panel>
  </a-collapse>
</template>

<script setup>
const manifestScope = defineModel('manifestScope', { type: String, default: 'binding' })
const preRefreshSeconds = defineModel('preRefreshSeconds', { type: Number, default: 0 })
const retryOnceOn401 = defineModel('retryOnceOn401', { type: Boolean, default: false })

defineProps({
  readonly: { type: Boolean, default: false }
})
</script>

<style lang="less" scoped>
.auth-advanced-collapse {
  border-radius: 8px;
  background: var(--gray-25);
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

  &.compact {
    flex-direction: row;
    align-items: center;
    gap: 10px;
  }

  &.span-2 {
    grid-column: 1 / -1;
  }

  label {
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
  }
}

.field-helper {
  color: var(--gray-500);
  font-size: 12px;
}

@media (max-width: 720px) {
  .auth-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
