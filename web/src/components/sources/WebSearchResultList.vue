<template>
  <div class="web-search-result-list">
    <div v-if="results.length > 0" class="search-results">
      <div
        v-for="(result, index) in results"
        :key="getItemKey(result, index)"
        class="search-result-item"
      >
        <div class="result-header">
          <h5 class="result-title">
            <a :href="result.url" target="_blank" rel="noopener noreferrer">
              {{ result.title }}
            </a>
          </h5>
          <span v-if="typeof result.score === 'number'" class="result-score">
            相关度: {{ (result.score * 100).toFixed(1) }}%
          </span>
        </div>

        <div v-if="result.content" class="result-content">
          {{ result.content }}
        </div>
      </div>
    </div>

    <div v-else class="no-results">
      <p>{{ emptyText }}</p>
    </div>
  </div>
</template>

<script setup>
defineProps({
  results: {
    type: Array,
    default: () => []
  },
  emptyText: {
    type: String,
    default: '未找到相关搜索结果'
  }
})

const getItemKey = (item, index) => {
  if (item?.url) return item.url
  if (item?.title) return `${item.title}-${index}`
  return `${index}`
}
</script>

<style scoped lang="less">
.web-search-result-list {
  .search-results {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .search-result-item {
    padding: 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);

    .result-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;

      .result-title {
        margin: 0;
        font-size: 14px;
        line-height: 1.4;
        flex: 1;

        a {
          color: var(--main-color);
          text-decoration: none;
          font-weight: 500;

          &:hover {
            text-decoration: underline;
          }
        }
      }

      .result-score {
        font-size: 11px;
        color: var(--gray-600);
        background: var(--gray-50);
        padding: 0 6px;
        border-radius: 10px;
        white-space: nowrap;
      }
    }

    .result-content {
      font-size: 12px;
      line-height: 1.5;
      color: var(--gray-700);
      overflow: hidden;
      display: -webkit-box;
      line-clamp: 2;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
  }

  .no-results {
    text-align: center;
    color: var(--gray-500);
    padding: 12px;
    font-size: 12px;
  }
}
</style>
