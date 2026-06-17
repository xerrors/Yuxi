<template>
  <section class="auth-config-section">
    <div class="section-heading">
      <span>注入到 MCP</span>
      <small>运行时会把 token、密钥或上下文写入请求头或环境变量。</small>
    </div>
    <div class="auth-field-row compact">
      <label>注入目标</label>
      <a-segmented
        v-model:value="target"
        :disabled="readonly"
        :options="targetOptions"
        size="small"
      />
    </div>
    <div v-if="!readonly" class="quick-template-bar">
      <button
        v-for="entry in quickEntries"
        :key="`${entry.name}:${entry.value_template}`"
        type="button"
        @click="addQuickEntry(entry)"
      >
        {{ entry.label }}
      </button>
    </div>
    <McpAuthRowEditor
      v-model="entries"
      :readonly="readonly"
      first-field="name"
      second-field="value_template"
      first-placeholder="名称，如 Authorization"
      second-placeholder="模板，如 Bearer ${access_token}"
      add-text="添加注入项"
      row-key-prefix="inject"
    />
  </section>
</template>

<script setup>
import McpAuthRowEditor from './McpAuthRowEditor.vue'

const target = defineModel('target', { type: String, default: 'headers' })
const entries = defineModel('entries', { type: Array, required: true })

const props = defineProps({
  readonly: { type: Boolean, default: false },
  targetOptions: { type: Array, default: () => [] },
  quickEntries: { type: Array, default: () => [] }
})

const addQuickEntry = (entry) => {
  if (props.readonly) return
  const existing = entries.value.find((item) => item.name === entry.name)
  if (existing) {
    existing.value_template = entry.value_template
    return
  }
  entries.value.push({
    name: entry.name,
    value_template: entry.value_template
  })
}
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

  label {
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
  }
}

.quick-template-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;

  button {
    padding: 3px 8px;
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-25);
    color: var(--gray-600);
    cursor: pointer;
    font-size: 12px;

    &:hover {
      border-color: var(--main-color);
      color: var(--main-color);
    }
  }
}
</style>
