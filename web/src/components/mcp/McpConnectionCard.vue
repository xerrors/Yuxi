<template>
  <div class="connection-card" :class="{ 'is-panel': variant === 'panel' }">
    <div class="connection-card-header">
      <div class="connection-key-info">
        <KeyRound :size="18" class="connection-key-icon" />
        <div class="connection-key-copy">
          <h4>{{ title }}</h4>
          <span v-if="subtitle">{{ subtitle }}</span>
        </div>
      </div>
      <span
        v-if="showScopeBadge"
        class="connection-scope-badge"
        :class="[`scope-${connection?.scope_type || 'unknown'}`, { 'is-mismatch': scopeMismatch }]"
        :title="`生效范围：${scopeLabel}`"
      >
        <Globe2 v-if="connection?.scope_type === 'system'" :size="13" />
        <Building2 v-else-if="connection?.scope_type === 'department'" :size="13" />
        <UserRound v-else :size="13" />
        {{ scopeLabel }}
      </span>
    </div>

    <div class="connection-card-content">
      <McpConnectionIssue
        :issue="issue"
        :loading="issueLoading"
        @action="$emit('issue-action', connection)"
      />
      <div v-if="scopeTargetLabel" class="connection-info-item">
        <span class="info-label">绑定对象:</span>
        <span class="info-value">{{ scopeTargetLabel }}</span>
      </div>
      <div class="connection-info-item">
        <span class="info-label">最近记录:</span>
        <span class="info-value">{{ lastInfo }}</span>
      </div>
    </div>

    <div class="connection-card-footer">
      <div class="footer-left">
        <span class="switch-label">{{ statusSwitchLabel }}</span>
        <a-tooltip :title="statusToggleTooltip">
          <span class="status-switch-wrap">
            <a-switch
              size="small"
              :checked="connection?.status === 'active'"
              :disabled="!canToggleStatus"
              :loading="statusLoading"
              @change="(checked) => $emit('toggle-status', connection, checked)"
            />
          </span>
        </a-tooltip>
      </div>
      <div class="connection-row-actions">
        <a-button
          type="text"
          size="small"
          class="connection-action-btn lucide-icon-btn"
          @click="$emit('edit', connection)"
        >
          <Pencil :size="14" />
          <span>编辑</span>
        </a-button>
        <a-tooltip :title="testTooltip">
          <span class="connection-action-wrap">
            <a-button
              type="text"
              size="small"
              class="connection-action-btn lucide-icon-btn"
              :disabled="!canTest"
              :loading="testLoading"
              @click="$emit('test', connection)"
            >
              <Zap :size="14" />
              <span>测试</span>
            </a-button>
          </span>
        </a-tooltip>
        <a-tooltip :title="reauthorizeTooltip">
          <span class="connection-action-wrap">
            <a-button
              type="text"
              size="small"
              class="connection-action-btn lucide-icon-btn"
              :disabled="!canReauthorize"
              :loading="reauthorizeLoading"
              @click="$emit('reauthorize', connection)"
            >
              <RotateCw :size="14" />
              <span>重连</span>
            </a-button>
          </span>
        </a-tooltip>
        <a-button
          type="text"
          size="small"
          danger
          class="connection-action-btn danger-action-btn lucide-icon-btn"
          @click="$emit('delete', connection)"
        >
          <Trash2 :size="14" />
          <span>删除</span>
        </a-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import {
  Building2,
  Globe2,
  KeyRound,
  Pencil,
  RotateCw,
  Trash2,
  UserRound,
  Zap
} from 'lucide-vue-next'
import McpConnectionIssue from './McpConnectionIssue.vue'

defineProps({
  connection: { type: Object, required: true },
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  scopeLabel: { type: String, default: '未知范围' },
  scopeTargetLabel: { type: String, default: '' },
  scopeMismatch: { type: Boolean, default: false },
  showScopeBadge: { type: Boolean, default: true },
  issue: { type: Object, default: null },
  lastInfo: { type: String, default: '暂无记录' },
  statusSwitchLabel: { type: String, default: '未知状态' },
  statusToggleTooltip: { type: String, default: '' },
  testTooltip: { type: String, default: '' },
  reauthorizeTooltip: { type: String, default: '' },
  canToggleStatus: { type: Boolean, default: false },
  canTest: { type: Boolean, default: false },
  canReauthorize: { type: Boolean, default: false },
  statusLoading: { type: Boolean, default: false },
  testLoading: { type: Boolean, default: false },
  reauthorizeLoading: { type: Boolean, default: false },
  issueLoading: { type: Boolean, default: false },
  variant: { type: String, default: 'card' }
})

defineEmits(['edit', 'test', 'reauthorize', 'delete', 'toggle-status', 'issue-action'])
</script>

<style lang="less" scoped>
.connection-card {
  min-width: 0;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  padding: 14px;
  background: var(--gray-0);
  transition:
    border-color 0.2s,
    box-shadow 0.2s;

  &:hover {
    border-color: var(--gray-300);
  }

  &.is-panel {
    border-radius: 8px;
    padding: 12px;
  }
}

.connection-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.connection-key-info {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.connection-key-icon {
  color: var(--main-600);
  flex-shrink: 0;
}

.connection-key-copy {
  min-width: 0;

  h4 {
    margin: 0;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
    line-height: 1.4;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  span {
    display: block;
    margin-top: 2px;
    color: var(--gray-500);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.connection-scope-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 26px;
  flex-shrink: 0;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-25);
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;

  &.scope-system {
    border-color: var(--color-success-100);
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.scope-department {
    border-color: var(--color-accent-100);
    background: var(--color-accent-50);
    color: var(--color-accent-700);
  }

  &.scope-user {
    border-color: var(--color-info-100);
    background: var(--color-info-50);
    color: var(--color-info-700);
  }

  &.is-mismatch {
    border-style: dashed;
    opacity: 0.75;
  }
}

.connection-card-content {
  margin-bottom: 12px;
}

.connection-info-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 6px;
  color: var(--gray-900);
  font-size: 13px;

  &:last-child {
    margin-bottom: 0;
  }
}

.info-label {
  color: var(--gray-600);
  flex-shrink: 0;
}

.info-value {
  min-width: 0;
  color: var(--gray-900);
  word-break: break-all;
}

.connection-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--gray-100);
}

.footer-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.switch-label {
  color: var(--gray-600);
  font-size: 12px;
}

.status-switch-wrap,
.connection-action-wrap {
  display: inline-flex;
  align-items: center;
}

.connection-row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  flex-wrap: wrap;
}

.connection-action-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--gray-700);
  font-size: 12px;

  &:hover {
    color: var(--main-600);
  }
}

.danger-action-btn {
  color: var(--color-error-700);

  &:hover {
    background: var(--color-error-50);
    color: var(--color-error-900);
  }
}

@media (max-width: 900px) {
  .connection-row-actions {
    justify-content: flex-start;
  }

  .connection-card-footer {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
