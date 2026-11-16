<template>
  <div class="source-section">
    <div class="section-title">网络搜索来源 ({{ sources.length }})</div>
    <WebSearchResultList :results="sources" empty-text="未找到网络搜索来源" />
  </div>
</template>

<script setup>
import WebSearchResultList from '@/components/sources/WebSearchResultList.vue'

defineProps({
  sources: {
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
