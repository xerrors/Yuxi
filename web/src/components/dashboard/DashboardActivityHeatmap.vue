<template>
  <div class="activity-heatmap" :aria-busy="loading">
    <div v-if="loading" class="heatmap-loading" aria-label="正在加载活跃度">
      <span v-for="index in 126" :key="index" class="heatmap-loading-cell" />
    </div>
    <div v-else-if="!weeks.length" class="heatmap-empty">暂无活跃记录</div>
    <template v-else>
      <div class="heatmap-body">
        <div class="heatmap-weekday-labels" aria-hidden="true">
          <span>一</span>
          <span>三</span>
          <span>五</span>
        </div>
        <div class="heatmap-scroll">
          <div class="heatmap-months" :style="heatmapGridStyle" aria-hidden="true">
            <span
              v-for="month in months"
              :key="month.key"
              :style="{ gridColumn: `${month.start + 1} / span ${month.span}` }"
            >
              {{ month.label }}
            </span>
          </div>
          <div
            class="heatmap-grid"
            :style="heatmapGridStyle"
            role="grid"
            aria-label="近 120 天用户活跃度"
          >
            <div
              v-for="(week, weekIndex) in weeks"
              :key="weekIndex"
              class="heatmap-week"
              role="row"
            >
              <span
                v-for="(cell, cellIndex) in week"
                :key="cell?.date || `empty-${weekIndex}-${cellIndex}`"
                class="heatmap-cell"
                :class="`level-${cell?.level ?? 0}`"
                :title="cell ? `${cell.date}：${cell.value} 位活跃用户` : ''"
                :aria-label="cell ? `${cell.date}，${cell.value} 位活跃用户` : undefined"
                role="gridcell"
              />
            </div>
          </div>
        </div>
      </div>
      <div class="heatmap-footer">
        <span>少</span>
        <span
          v-for="level in 5"
          :key="level"
          class="heatmap-legend-cell"
          :class="`level-${level - 1}`"
        />
        <span>多</span>
        <span class="heatmap-total">{{ totalActiveUsers }} 次活跃记录</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { buildHeatmapMonthSegments } from '@/utils/dashboard'

const props = defineProps({
  data: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  }
})

const CELL_SIZE = 16
const CELL_GAP = 3

const normalizedDays = computed(() =>
  props.data
    .map((item) => ({
      date: String(item?.date || ''),
      value: Number(item?.active_users || 0)
    }))
    .filter((item) => item.date)
)

const maxValue = computed(() => Math.max(...normalizedDays.value.map((item) => item.value), 0))

const getLevel = (value) => {
  if (!maxValue.value || value <= 0) return 0
  return Math.min(4, Math.ceil((value / maxValue.value) * 4))
}

const weeks = computed(() => {
  if (!normalizedDays.value.length) return []
  const days = normalizedDays.value.map((item) => ({ ...item, level: getLevel(item.value) }))
  const firstDate = new Date(`${days[0].date}T00:00:00`)
  const mondayOffset = (firstDate.getDay() + 6) % 7
  const padded = [...Array(mondayOffset).fill(null), ...days]
  const weekList = []
  for (let index = 0; index < padded.length; index += 7) {
    const week = padded.slice(index, index + 7)
    while (week.length < 7) week.push(null)
    weekList.push(week)
  }
  return weekList
})

const heatmapGridStyle = computed(() => ({
  '--heatmap-weeks': weeks.value.length,
  minWidth: `${Math.max(weeks.value.length * (CELL_SIZE + CELL_GAP) - CELL_GAP, 0)}px`
}))

const months = computed(() => buildHeatmapMonthSegments(weeks.value))

const totalActiveUsers = computed(() =>
  normalizedDays.value.reduce((sum, item) => sum + item.value, 0)
)
</script>

<style lang="less" scoped>
.activity-heatmap {
  width: 100%;
  min-width: 0;
  padding-top: 2px;
}

.heatmap-loading {
  display: grid;
  width: 100%;
  grid-template-columns: repeat(18, 16px);
  justify-content: space-between;
  gap: 3px 0;
  min-height: 130px;
}

.heatmap-loading-cell,
.heatmap-cell,
.heatmap-legend-cell {
  display: block;
  border-radius: 2px;
  background: var(--gray-100);
}

.heatmap-loading-cell {
  width: 16px;
  height: 16px;
  animation: heatmap-pulse 1.2s ease-in-out infinite alternate;
}

.heatmap-empty {
  display: grid;
  min-height: 88px;
  place-items: center;
  color: var(--gray-500);
  font-size: 12px;
}

.heatmap-body {
  display: flex;
  min-width: 0;
  gap: 6px;
}

.heatmap-weekday-labels {
  display: grid;
  width: 22px;
  flex: 0 0 22px;
  grid-template-rows: 18px repeat(7, 16px);
  gap: 3px;
  color: var(--gray-500);
  font-size: 10px;
  line-height: 10px;

  span:nth-child(1) {
    grid-row: 2;
  }

  span:nth-child(2) {
    grid-row: 4;
  }

  span:nth-child(3) {
    grid-row: 6;
  }
}

.heatmap-scroll {
  min-width: 0;
  flex: 1;
  overflow-x: auto;
  scrollbar-width: thin;
}

.heatmap-months,
.heatmap-grid {
  display: grid;
  width: 100%;
  grid-template-columns: repeat(var(--heatmap-weeks), 16px);
  justify-content: space-between;
}

.heatmap-months {
  height: 18px;
  color: var(--gray-500);
  font-size: 10px;

  span {
    min-width: 0;
    white-space: nowrap;
  }
}

.heatmap-week {
  display: grid;
  grid-template-rows: repeat(7, 16px);
  gap: 3px;
}

.heatmap-cell {
  width: 16px;
  height: 16px;
  transition: filter 0.2s ease;

  &:hover {
    filter: brightness(0.88);
  }
}

.heatmap-footer {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 4px;
  margin-top: 10px;
  color: var(--gray-500);
  font-size: 10px;
}

.heatmap-legend-cell {
  width: 12px;
  height: 12px;
}

.heatmap-total {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.level-0 {
  background: var(--gray-100);
}

.level-1 {
  background: color-mix(in srgb, var(--main-color) 20%, var(--gray-0));
}

.level-2 {
  background: color-mix(in srgb, var(--main-color) 40%, var(--gray-0));
}

.level-3 {
  background: color-mix(in srgb, var(--main-color) 65%, var(--gray-0));
}

.level-4 {
  background: var(--main-color);
}

@keyframes heatmap-pulse {
  from {
    opacity: 0.45;
  }

  to {
    opacity: 0.9;
  }
}

@media (prefers-reduced-motion: reduce) {
  .heatmap-loading-cell {
    animation: none;
  }
}
</style>
