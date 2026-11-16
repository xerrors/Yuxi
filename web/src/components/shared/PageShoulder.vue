<template>
  <div class="page-shoulder">
    <div class="page-shoulder-left">
      <a-input
        v-if="!$slots.search"
        v-model:value="searchModel"
        :placeholder="searchPlaceholder"
        allow-clear
        class="search-input"
      >
        <template #prefix><Search :size="14" class="text-muted" /></template>
      </a-input>
      <slot name="search" />
      <slot name="filters" />
    </div>
    <div v-if="$slots.actions" class="page-shoulder-right">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
import { Search } from '@lucide/vue'

const searchModel = defineModel('search', { type: String, default: '' })

defineProps({
  searchPlaceholder: { type: String, default: '搜索...' }
})
</script>

<style lang="less" scoped>
.page-shoulder {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px var(--page-padding) 0;

  &-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  &-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.search-input {
  width: 280px;
  display: flex;
  align-items: center;

  :deep(.ant-input-affix-wrapper) {
    height: 32px;
    padding: 0 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background-color: var(--gray-0);

    &:hover,
    &:focus,
    &.ant-input-affix-wrapper-focused {
      border-color: var(--gray-200);
      box-shadow: none;
    }
  }

  :deep(.ant-input-prefix) {
    margin-right: 8px;
    color: var(--gray-400);
  }

  :deep(.ant-input) {
    height: 100%;
    background-color: transparent;
  }
}

.text-muted {
  color: var(--gray-400);
}
</style>
