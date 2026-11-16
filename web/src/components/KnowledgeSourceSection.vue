<template>
  <div class="source-section">
    <div class="section-title">知识库来源 ({{ chunks.length }})</div>
    <KbResultGroupedList :chunks="chunks" :show-summary="false" />
  </div>
</template>

<script setup>
import KbResultGroupedList from '@/components/sources/KbResultGroupedList.vue'

defineProps({
  chunks: {
    type: Array,
    default: () => []
  }
})
</script>

<style scoped lang="less">
.source-section {
  .section-title {
    font-size: 12px;
    color: var(--gray-700);
    margin-bottom: 8px;
    font-weight: 600;
  }
}
</style>
