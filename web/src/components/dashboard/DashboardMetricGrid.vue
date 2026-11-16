<template>
  <div class="dashboard-metric-grid">
    <slot />
  </div>
</template>

<style lang="less">
.dashboard-metric-grid {
  display: grid;
  min-width: 0;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 168px), 1fr));
  gap: 12px;
}
</style>
