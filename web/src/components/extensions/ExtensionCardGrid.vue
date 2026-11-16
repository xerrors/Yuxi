<template>
  <div
    class="extension-card-grid"
    :style="{ gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` }"
  >
    <slot />
    <div v-if="!$slots.default && items.length === 0" class="extension-card-grid-empty">
      <a-empty :image="false" :description="emptyText" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  emptyText: { type: String, default: '暂无数据' },
  minWidth: { type: Number, default: 280 }
})
</script>

<style lang="less" scoped>
.extension-card-grid {
  display: grid;
  gap: 16px;
  padding: 16px var(--page-padding);

  &-empty {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 60px 0;
  }
}
</style>
