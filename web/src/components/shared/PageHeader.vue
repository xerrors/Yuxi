<template>
  <div class="page-header" :class="{ 'page-header--bordered': showBorder }">
    <div v-if="loading" class="page-header-loading-bar-wrapper">
      <div class="page-header-loading-bar"></div>
    </div>
    <div class="page-header-left">
      <h1 class="page-header-title">{{ title }}</h1>
      <nav v-if="tabs.length > 0" class="page-header-tabs" :aria-label="ariaLabel">
        <template v-for="item in tabs" :key="item.key">
          <RouterLink
            v-if="item.path"
            :to="item.path"
            class="tab-item"
            :class="{ active: activeKey === item.key }"
            @click="emitChange(item)"
          >
            {{ item.label }}
          </RouterLink>
          <button
            v-else
            type="button"
            class="tab-item"
            :class="{ active: activeKey === item.key }"
            @click="emitChange(item)"
          >
            {{ item.label }}
          </button>
        </template>
      </nav>
    </div>
    <div v-if="$slots.info || $slots.actions" class="page-header-right">
      <slot name="info" />
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
import { RouterLink } from 'vue-router'

defineProps({
  title: { type: String, required: true },
  activeKey: { type: String, default: '' },
  tabs: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  showBorder: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '视图切换' }
})

const emit = defineEmits(['update:activeKey', 'change'])

function emitChange(item) {
  emit('update:activeKey', item.key)
  emit('change', item)
}
</script>

<style scoped lang="less">
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: 48px;
  padding: 0 var(--page-padding);
  background-color: var(--light-60);
  backdrop-filter: blur(10px);
  position: sticky;
  top: 0;
  z-index: 1000;

  &--bordered {
    border-bottom: 1px solid var(--gray-100);
  }
}

.page-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.page-header-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--gray-2000);
  white-space: nowrap;
}

.page-header-tabs {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding-left: 12px;
  margin-left: 2px;
  border-left: 1px solid var(--gray-200);
  height: 18px;
  line-height: 18px;
  flex-shrink: 0;
}

.tab-item {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-600);
  font-size: 14px;
  font-weight: 500;
  line-height: 1;
  text-decoration: none;
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    color 0.2s ease;

  &:hover {
    color: var(--gray-900);
    background-color: var(--gray-50);
  }

  &.active {
    color: var(--gray-2000);
    background-color: color-mix(in srgb, var(--gray-800) 6%, var(--gray-0));
  }
}

.page-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.page-header-loading-bar-wrapper {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  z-index: 101;
  overflow: hidden;
  background: transparent;

  .page-header-loading-bar {
    height: 100%;
    background: var(--main-color);
    width: 30%;
    position: absolute;
    animation: page-header-loading-bar-anim 1.5s infinite linear;
  }
}

@keyframes page-header-loading-bar-anim {
  0% {
    left: -30%;
  }
  100% {
    left: 100%;
  }
}
</style>
