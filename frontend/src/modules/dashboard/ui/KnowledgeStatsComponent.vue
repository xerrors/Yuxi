<template>
  <DashboardChartCard title="知识库使用情况" :loading="loading" class="dashboard-card">
    <!-- 知识库概览 -->
    <template #summary>
      <div class="dashboard-card-metric-grid">
        <DashboardMetricCard
          :icon="Database"
          :value="formatStatNumber(knowledgeStats, knowledgeStats?.total_knowledge_bases)"
          label="知识库总数"
          tone="info"
          compact
        />
        <DashboardMetricCard
          :icon="FileText"
          :value="formatStatNumber(knowledgeStats, knowledgeStats?.total_files)"
          label="文件总数"
          tone="success"
          compact
        />
        <DashboardMetricCard
          :icon="HardDrive"
          :value="formattedStorage.value"
          :label="knowledgeStats ? `存储容量 (${formattedStorage.unit})` : '存储容量'"
          tone="warning"
          compact
        />
      </div>
    </template>

    <div class="file-distribution">
      <h4>文件类型分布</h4>
      <template v-if="fileTypeData.length">
        <div class="donut-wrap">
          <div ref="fileTypeChartRef" class="file-type-chart"></div>
          <div class="donut-summary">
            <span>文件总数</span>
            <strong>{{ totalFiles.toLocaleString() }}</strong>
          </div>
        </div>
        <ul
          class="file-type-legend"
          :class="{ 'legend-many-types': fileTypeData.length >= 5 }"
          aria-label="文件类型数量与占比"
        >
          <li
            v-for="(item, index) in fileTypeData"
            :key="item.name"
            @mouseenter="highlightFileTypeSlice(index)"
            @mouseleave="downplayFileTypeSlice(index)"
          >
            <span class="legend-swatch" :style="{ backgroundColor: getColorByIndex(index) }"></span>
            <span class="legend-name" :title="item.name" tabindex="0">{{ item.name }}</span>
            <span class="legend-value">{{ item.value.toLocaleString() }}</span>
            <span class="legend-percent">{{
              item.value / totalFiles < 0.001
                ? '<0.1%'
                : `${((item.value / totalFiles) * 100).toFixed(1)}%`
            }}</span>
          </li>
        </ul>
      </template>
      <a-empty
        v-else
        :description="knowledgeStats ? '暂无文件类型数据' : '知识库统计暂不可用'"
      />
    </div>
  </DashboardChartCard>
</template>

<script setup>
import DashboardChartCard from './DashboardChartCard.vue'
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from '@/modules/dashboard/model/dashboardCharts'
import { getColorByIndex, getColorPalette } from '@/shared/lib/chartColors'
import { useThemeStore } from '@/shared/model/theme'
import { formatStatNumber, formatStorageSize } from '@/modules/dashboard/model/dashboard'
import { Database, FileText, HardDrive } from '@lucide/vue'
import DashboardMetricCard from './DashboardMetricCard.vue'

const props = defineProps({
  knowledgeStats: { type: Object, default: () => ({}) },
  loading: { type: Boolean, default: false }
})
const themeStore = useThemeStore()
const fileTypeChartRef = ref(null)
let fileTypeChart = null
let resizeObserver = null
const formattedStorage = computed(() =>
  props.knowledgeStats
    ? formatStorageSize(props.knowledgeStats.total_storage_size)
    : { value: '—', unit: '—' }
)
const fileTypeData = computed(() =>
  Object.entries(props.knowledgeStats?.file_type_distribution || {})
    .map(([name, value]) => ({ name: name || '未知', value: Number(value) }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value)
)
const totalFiles = computed(() => fileTypeData.value.reduce((sum, item) => sum + item.value, 0))

/** 悬停明细行时高亮对应扇区，与图内悬浮保持一致的反馈。 */
function highlightFileTypeSlice(index) {
  fileTypeChart?.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: index })
}

/** 离开明细行后取消扇区高亮。 */
function downplayFileTypeSlice(index) {
  fileTypeChart?.dispatchAction({ type: 'downplay', seriesIndex: 0, dataIndex: index })
}

/** 更新圆环，明细由独立 DOM 展示，避免图例覆盖图形。 */
async function updateChart() {
  await nextTick()
  resizeObserver?.disconnect()
  fileTypeChart?.dispose()
  fileTypeChart = null
  if (!fileTypeChartRef.value) return
  fileTypeChart = echarts.init(fileTypeChartRef.value)
  // 数量与占比由下方明细列表承载，圆环不再悬浮提示，避免遮挡中心总数。
  fileTypeChart.setOption({
    series: [
      {
        name: '文件类型',
        type: 'pie',
        radius: ['58%', '82%'],
        center: ['50%', '50%'],
        label: { show: false },
        labelLine: { show: false },
        data: fileTypeData.value,
        color: getColorPalette()
      }
    ]
  })
  resizeObserver = new ResizeObserver(() => fileTypeChart?.resize())
  resizeObserver.observe(fileTypeChartRef.value)
}

/** 释放图表实例与尺寸观察器。 */
function cleanup() {
  resizeObserver?.disconnect()
  fileTypeChart?.dispose()
  fileTypeChart = null
}
watch([() => props.knowledgeStats, () => props.loading, () => themeStore.isDark], updateChart, {
  deep: true
})
onMounted(updateChart)
onBeforeUnmount(cleanup)
defineExpose({ cleanup })
</script>

<style scoped lang="less">
.file-distribution {
  // 图例列数由卡片自身宽度决定，避免窄卡片里名称、数量与占比互相挤压。
  container-type: inline-size;
}
.file-distribution h4,
.legend-value {
  color: var(--color-text);
}
.donut-wrap {
  position: relative;
  height: 220px;
}
.file-type-chart {
  width: 100%;
  height: 100%;
}
.donut-summary {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  color: var(--color-text-secondary);
  font-size: 12px;
  strong {
    margin-top: 4px;
    font-size: 24px;
    color: var(--color-text);
  }
}
.file-type-legend {
  padding: 0;
  margin: 8px 0 0;
  list-style: none;
  li {
    display: grid;
    // 名称列优先分配空间；数值/百分比用内容宽度，避免固定 52px 挤压名称
    grid-template-columns: 8px minmax(0, 1fr) max-content max-content;
    align-items: center;
    gap: 10px;
    padding: 7px 0;
    border-bottom: 1px solid var(--gray-100);
    font-size: 13px;
  }
  // 类型较多时默认单列；容器足够宽时再拆两列，每个类型保持在一条明细内。
  &.legend-many-types {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-items: start;
  }
}
@container (min-width: 400px) {
  .file-type-legend.legend-many-types {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    column-gap: 20px;
  }
}
.legend-swatch {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}
.legend-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text);
  // 名称被截断时保留可发现性：悬停提示全名（原生 title）。
  cursor: help;

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
    border-radius: 2px;
  }
}
.legend-value,
.legend-percent {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.legend-percent {
  color: var(--color-text-secondary);
}
</style>
