<template>
  <a-form layout="vertical" class="mcp-connection-form" :class="`is-${variant}`">
    <div v-if="title" class="form-panel-header">
      <h4>{{ title }}</h4>
      <a-button type="text" class="lucide-icon-btn" @click="$emit('cancel')">
        <X :size="14" />
      </a-button>
    </div>

    <div class="connection-form-body">
      <section v-if="showScope" class="drawer-section">
        <div class="drawer-section-title">
          <span>绑定范围</span>
          <small>决定运行时为哪些请求使用这组凭据。</small>
        </div>
        <div class="scope-option-grid">
          <button
            v-for="option in availableScopeOptions"
            :key="option.value"
            type="button"
            class="scope-option"
            :class="{ active: form.scopeType === option.value }"
            :disabled="isEditing"
            @click="form.scopeType = option.value"
          >
            <component :is="option.icon" :size="16" />
            <span>{{ option.label }}</span>
            <small>{{ option.description }}</small>
          </button>
        </div>
        <a-form-item v-if="showScopeIdField" :label="scopeIdLabel" required class="form-item">
          <a-select
            v-if="form.scopeType === 'department'"
            v-model:value="form.scopeId"
            :disabled="isEditing"
            :loading="scopeOptionsLoading"
            placeholder="请选择部门"
            show-search
            :options="departmentSelectOptions"
          />
          <a-select
            v-else-if="form.scopeType === 'user'"
            v-model:value="form.scopeId"
            :disabled="isEditing"
            :loading="scopeOptionsLoading"
            placeholder="请选择用户"
            show-search
            :options="userSelectOptions"
          />
          <a-input
            v-else
            v-model:value="form.scopeId"
            :disabled="isEditing"
            :placeholder="scopeIdPlaceholder"
          />
        </a-form-item>
      </section>

      <section class="drawer-section">
        <div class="drawer-section-title">
          <span>展示信息</span>
          <small>名称用于列表识别，不参与鉴权计算。</small>
        </div>
        <a-form-item label="连接名称" class="form-item">
          <a-input v-model:value="form.displayName" :placeholder="displayNamePlaceholder" />
        </a-form-item>
      </section>

      <section class="drawer-section">
        <div class="drawer-section-title">
          <span>凭据</span>
          <small>{{ credentialHint }}</small>
        </div>
        <div v-if="secretFields.length > 0" class="secret-field-grid">
          <a-form-item
            v-for="fieldName in secretFields"
            :key="fieldName"
            :label="getSecretFieldLabel(fieldName)"
            class="form-item"
          >
            <a-input-password
              v-model:value="form.secretValues[fieldName]"
              :placeholder="isEditing ? '留空表示保持现有值' : `请输入 ${fieldName}`"
            />
          </a-form-item>
        </div>
        <a-form-item v-else label="长期凭据" class="form-item">
          <a-textarea
            v-model:value="form.credentialText"
            :rows="4"
            class="config-textarea"
            :placeholder="isEditing ? '留空表示保持现有凭据' : rawCredentialPlaceholder"
          />
        </a-form-item>
      </section>

      <a-collapse ghost class="connection-advanced-collapse">
        <a-collapse-panel key="advanced" header="高级设置">
          <a-form-item label="外部主体标识" class="form-item">
            <a-input v-model:value="form.externalSubject" :placeholder="externalSubjectPlaceholder" />
          </a-form-item>
          <a-form-item v-if="secretFields.length > 0" label="原始凭据 JSON" class="form-item">
            <a-textarea
              v-model:value="form.credentialText"
              :rows="advancedCredentialRows"
              class="config-textarea"
              placeholder='可选。填写后会覆盖上方密钥字段，例如 {"secrets":{"client_id":"xxx"}}'
            />
          </a-form-item>
          <a-form-item label="元数据 JSON" class="form-item">
            <a-textarea
              v-model:value="form.metaText"
              :rows="metaRows"
              class="config-textarea"
              placeholder='可选，例如 {"tenant":"finance"}'
            />
          </a-form-item>
        </a-collapse-panel>
      </a-collapse>

      <div class="connection-form-footer">
        <a-button @click="$emit('cancel')" :disabled="submitting">取消</a-button>
        <a-button type="primary" :loading="submitting" @click="$emit('submit')">
          {{ submitText }}
        </a-button>
      </div>
    </div>
  </a-form>
</template>

<script setup>
import { computed } from 'vue'
import { X } from 'lucide-vue-next'
import { getMcpSecretFieldLabel } from '@/utils/mcpConnectionUtils'

const form = defineModel({ type: Object, required: true })

const props = defineProps({
  title: { type: String, default: '' },
  variant: { type: String, default: 'drawer' },
  isEditing: { type: Boolean, default: false },
  submitting: { type: Boolean, default: false },
  showScope: { type: Boolean, default: false },
  showScopeIdField: { type: Boolean, default: false },
  availableScopeOptions: { type: Array, default: () => [] },
  departmentList: { type: Array, default: () => [] },
  userList: { type: Array, default: () => [] },
  scopeOptionsLoading: { type: Boolean, default: false },
  scopeIdLabel: { type: String, default: '范围标识' },
  scopeIdPlaceholder: { type: String, default: '' },
  secretFields: { type: Array, default: () => [] },
  credentialHint: { type: String, default: '' },
  displayNamePlaceholder: { type: String, default: '例如：财务部共享连接' },
  externalSubjectPlaceholder: { type: String, default: '可选，例如外部用户名或 tenant subject' },
  rawCredentialPlaceholder: { type: String, default: '粘贴长期 token' },
  advancedCredentialRows: { type: Number, default: 5 },
  metaRows: { type: Number, default: 4 },
  submitText: { type: String, default: '保存连接' }
})

defineEmits(['submit', 'cancel'])

const departmentSelectOptions = computed(() =>
  props.departmentList.map((department) => ({
    label: department.name,
    value: department.id.toString()
  }))
)

const userSelectOptions = computed(() =>
  props.userList.map((user) => ({
    label: user.username === user.user_id ? user.username : `${user.username} (${user.user_id})`,
    value: user.id.toString()
  }))
)

const getSecretFieldLabel = getMcpSecretFieldLabel
</script>

<style lang="less" scoped>
.mcp-connection-form {
  &.is-panel {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    overflow: hidden;
    background: var(--gray-0);
  }
}

.form-panel-header {
  height: 48px;
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--gray-150);

  h4 {
    margin: 0;
    color: var(--gray-900);
    font-size: 15px;
    font-weight: 600;
  }
}

.connection-form-body {
  padding: 18px;
}

.is-panel .connection-form-body {
  padding: 14px;
}

.drawer-section {
  & + .drawer-section {
    margin-top: 18px;
    padding-top: 18px;
    border-top: 1px solid var(--gray-100);
  }
}

.drawer-section-title {
  margin-bottom: 12px;
  display: flex;
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
  }
}

.scope-option-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 14px;
}

.scope-option {
  min-height: 78px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 10px;
  background: var(--gray-0);
  color: var(--gray-700);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  text-align: left;
  cursor: pointer;

  &:hover:not(:disabled),
  &.active {
    border-color: var(--main-300);
    background: var(--main-10);
    color: var(--main-700);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.72;
  }

  span {
    font-size: 13px;
    font-weight: 600;
  }

  small {
    color: var(--gray-500);
    font-size: 12px;
  }
}

.secret-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.config-textarea {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
}

.connection-advanced-collapse {
  margin-top: 2px;
}

.connection-form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
}

@media (max-width: 900px) {
  .scope-option-grid,
  .secret-field-grid {
    grid-template-columns: 1fr;
  }
}
</style>
