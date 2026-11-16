<template>
  <BaseToolCall :tool-call="toolCall">
    <template #header>
      <div class="sep-header">
        <span class="note">计算器</span>
      </div>
    </template>
    <template #result="{ resultContent }">
      <div class="calculator-result">
        <!-- <div class="calc-header">
          <h4><NumberOutlined /> 计算结果</h4>
        </div> -->

        <div class="calc-display">
          <div class="result-container">
            <div class="result-value">{{ formatNumber(parseData(resultContent)) }}</div>
          </div>
        </div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import BaseToolCall from '../BaseToolCall.vue'

defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const parseData = (content) => {
  if (typeof content === 'string') {
    try {
      return JSON.parse(content)
    } catch {
      return content
    }
  }
  return content
}

// 方法
const formatNumber = (num) => {
  if (typeof num !== 'number') return String(num)

  // 处理特殊值
  if (!isFinite(num)) {
    if (num === Infinity) return '∞'
    if (num === -Infinity) return '-∞'
    if (isNaN(num)) return 'NaN'
  }

  // 使用本地化格式
  return new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: 10,
    useGrouping: true
  }).format(num)
}
</script>

<style lang="less" scoped>
.calculator-result {
  background: var(--gray-25);
  border-radius: 8px;

  .calc-header {
    padding: 12px 16px;

    h4 {
      margin: 0;
      color: var(--main-color);
      font-size: 14px;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 6px;
    }
  }

  .calc-display {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px;

    .result-container {
      display: flex;
      align-items: center;
      gap: 10px;
      flex: 1;

      .result-label {
        font-size: 13px;
        color: var(--gray-600);
        font-weight: 500;
      }

      .result-value {
        font-size: 1rem;
        font-weight: 600;
        color: var(--main-color);
        font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
        word-break: break-all;
      }
    }

    .result-actions {
      display: flex;
      gap: 6px;
    }
  }
}
</style>
