<template>
  <div class="extension-detail-shell extension-detail-page">
    <div v-if="loading" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>

    <a-tabs
      v-if="ready"
      :active-key="activeKey"
      :class="{ 'extension-detail-tabs-no-actions': !$slots.actions }"
      class="minimal-tabs extension-detail-tabs"
      @change="emit('update:activeKey', $event)"
    >
      <template #leftExtra>
        <slot name="breadcrumb" />
      </template>
      <template v-if="$slots.actions" #rightExtra>
        <slot name="actions" />
      </template>

      <a-tab-pane v-for="tab in tabs" :key="tab.key" :force-render="tab.forceRender === true">
        <template #tab>
          <span
            class="extension-detail-tab-title"
            :aria-label="tab.label"
            :title="tab.label"
          >
            <component
              :is="tab.icon"
              v-if="tab.icon"
              class="extension-detail-tab-icon"
              :size="14"
              aria-hidden="true"
            />
            <span class="extension-detail-tab-label">{{ tab.label }}</span>
          </span>
        </template>
        <div class="extension-detail-panel" :class="tab.panelClass">
          <slot :name="`panel-${tab.key}`" :tab="tab" />
        </div>
      </a-tab-pane>
    </a-tabs>

    <div v-else-if="!loading" class="extension-detail-empty">
      <slot name="empty">
        <a-empty :description="emptyDescription" />
      </slot>
    </div>

    <slot name="overlays" />
  </div>
</template>

<script setup>
defineProps({
  activeKey: { type: String, required: true },
  tabs: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  ready: { type: Boolean, default: false },
  emptyDescription: { type: String, default: '未找到扩展' }
})

const emit = defineEmits(['update:activeKey'])
</script>

<style lang="less" scoped>
.extension-detail-shell {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--gray-0);
}

.loading-bar-wrapper {
  position: absolute;
  inset: 0 0 auto;
  height: 2px;
  z-index: 100;
  overflow: hidden;
  background: transparent;
}

.loading-bar {
  position: absolute;
  width: 30%;
  height: 100%;
  background: var(--main-color);
  animation: extension-loading-bar 1.5s infinite linear;
}

@keyframes extension-loading-bar {
  from {
    left: -30%;
  }

  to {
    left: 100%;
  }
}

.extension-detail-tabs {
  height: 100%;
  min-height: 0;
}

:deep(.extension-detail-tabs > .ant-tabs-nav) {
  width: 100%;
  min-height: 44px;
  margin: 0;
  padding: 0 24px;
  box-sizing: border-box;
  border-bottom: 0;
  gap: 12px;
}

:deep(.extension-detail-tabs > .ant-tabs-nav::before) {
  border-bottom: 0;
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-extra-content) {
  min-width: 0;
  flex: 1 1 0;
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-extra-content:last-child) {
  display: flex;
  justify-content: flex-end;
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-nav-wrap) {
  height: 30px;
  flex: 0 0 auto;
  align-self: center;
  justify-content: center;
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-nav-list) {
  gap: 4px;
  padding: 0;
  border-radius: 0;
  background: transparent;
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-tab) {
  height: 30px;
  margin: 0;
  padding: 5px 11px;
  border: 0;
  border-radius: 999px;
  color: var(--gray-600);
  font-size: 13px;
  font-weight: 500;
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-tab:hover) {
  color: var(--gray-900);
  background: var(--gray-50);
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-tab-active) {
  background: var(--gray-100);
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-tab-active .ant-tabs-tab-btn) {
  color: var(--gray-900);
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-tab-btn:focus-visible) {
  outline: 2px solid var(--main-color);
  outline-offset: 3px;
}

:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-ink-bar),
:deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-nav-operations) {
  display: none;
}

:deep(.extension-detail-tabs > .ant-tabs-content-holder),
:deep(.extension-detail-tabs > .ant-tabs-content-holder > .ant-tabs-content),
:deep(.extension-detail-tabs > .ant-tabs-content-holder > .ant-tabs-content > .ant-tabs-tabpane) {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.extension-detail-tab-title {
  display: inline-flex;
  align-items: center;
  gap: 0;
  font-size: 13px;
  line-height: 18px;
}

.extension-detail-tab-icon {
  display: none;
}

.extension-detail-panel {
  height: 100%;
  min-height: 0;
  overflow-y: auto;

  &.extension-detail-panel-fixed {
    overflow: hidden;
  }
}

.extension-detail-empty {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

:deep(.extension-detail-breadcrumb) {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--gray-400);
}

:deep(.extension-detail-back) {
  margin: 0;
  padding: 5px 4px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-600);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

:deep(.extension-detail-back:hover),
:deep(.extension-detail-back:focus-visible) {
  color: var(--main-color);
  background: var(--main-10);
  outline: none;
}

:deep(.extension-detail-current) {
  min-width: 0;
  overflow: hidden;
  color: var(--gray-900);
  font-size: 15px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.extension-detail-actions) {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

:deep(.extension-detail-view) {
  width: min(calc(100% - 48px), 768px);
  margin: 48px auto;
  box-sizing: border-box;
}

:deep(.extension-detail-gray-switches .ant-switch) {
  background: var(--gray-300);
}

:deep(.extension-detail-gray-switches .ant-switch:hover:not(.ant-switch-disabled)) {
  background: var(--gray-400);
}

:deep(.extension-detail-gray-switches .ant-switch.ant-switch-checked) {
  background: var(--gray-700);
}

:deep(
  .extension-detail-gray-switches .ant-switch.ant-switch-checked:hover:not(.ant-switch-disabled)
) {
  background: var(--gray-800);
}

:deep(.extension-detail-section + .extension-detail-section) {
  margin-top: 34px;
}

:deep(.extension-detail-section-header) {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 14px;
}

:deep(.extension-detail-section-heading) {
  min-width: 0;
}

:deep(.extension-detail-section-heading h3) {
  margin: 0 0 4px;
  color: var(--gray-900);
  font-size: 15px;
  font-weight: 700;
}

:deep(.extension-detail-section-heading p) {
  margin: 0;
  color: var(--gray-500);
  font-size: 13px;
  line-height: 1.5;
}

:deep(.extension-detail-divider-list) {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--gray-150);
}

:deep(.extension-detail-divider-row) {
  border-bottom: 1px solid var(--gray-150);
  background: transparent;
}

@media (max-width: 900px) {
  :deep(.extension-detail-tabs > .ant-tabs-nav .ant-tabs-tab) {
    width: 30px;
    height: 30px;
    padding: 0;
    justify-content: center;
  }

  .extension-detail-tab-icon {
    display: block;
  }

  .extension-detail-tab-label {
    display: none;
  }

  :deep(.extension-detail-actions .extension-panel-action) {
    width: 30px;
    padding: 0;
  }

  :deep(.extension-detail-actions .extension-panel-action span) {
    display: none;
  }
}

@media (max-width: 768px) {
  :deep(.extension-detail-tabs > .ant-tabs-nav) {
    padding: 0 16px;
    gap: 10px;
  }

  :deep(.extension-detail-tabs-no-actions > .ant-tabs-nav .ant-tabs-extra-content:first-child) {
    flex: 1 1 auto;
  }

  :deep(.extension-detail-tabs-no-actions > .ant-tabs-nav .ant-tabs-nav-wrap) {
    justify-content: flex-end;
  }

  :deep(.extension-detail-view) {
    width: min(calc(100% - 32px), 768px);
    margin: 48px auto;
  }
}
</style>
