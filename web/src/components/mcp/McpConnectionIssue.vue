<template>
  <div v-if="issue" class="connection-issue" :class="`issue-${issue.tone}`">
    <div class="issue-copy">
      <span>问题：{{ issue.label }}</span>
      <small>{{ issue.description }}</small>
    </div>
    <a-button
      type="link"
      size="small"
      class="issue-action"
      :loading="loading"
      @click="$emit('action')"
    >
      {{ issue.actionLabel }}
    </a-button>
  </div>
</template>

<script setup>
defineProps({
  issue: { type: Object, default: null },
  loading: { type: Boolean, default: false }
})

defineEmits(['action'])
</script>

<style lang="less" scoped>
.connection-issue {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  padding: 9px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);

  &.issue-warning {
    border-color: var(--color-warning-100);
    background: var(--color-warning-10);

    .issue-copy span {
      color: var(--color-warning-900);
    }
  }

  &.issue-error {
    border-color: var(--color-error-100);
    background: var(--color-error-10);

    .issue-copy span {
      color: var(--color-error-700);
    }
  }
}

.issue-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;

  span {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 600;
    line-height: 1.4;
  }

  small {
    overflow: hidden;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.4;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.issue-action {
  flex-shrink: 0;
  padding: 0;
  font-size: 12px;
  font-weight: 500;
}

@media (max-width: 900px) {
  .connection-issue {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
