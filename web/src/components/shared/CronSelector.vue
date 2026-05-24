<script setup>
import { ref, watch, computed } from 'vue'
import { Clock, Check, AlertCircle, Info, Calendar } from 'lucide-vue-next'

// ============ Props & Emits ============
const props = defineProps({
  value: {
    type: String,
    default: '0 9 * * 1-5'
  }
})

const emit = defineEmits(['update:value'])

// ============ Internal States ============
const visible = ref(false)
const isAdvancedMode = ref(false) // true: 高级自定义, false: 快捷配置
const quickType = ref('daily') // interval | daily | weekly | monthly

const cronIntervalMins = ref(15)
const cronHour = ref(9)
const cronMinute = ref(0)
const cronDayOfWeek = ref([1, 2, 3, 4, 5])
const cronDayOfMonth = ref(1) // 恢复为单个数字单选

const tempCronExpr = ref(props.value)

// Options
const weekBadgeOptions = [
  { label: '一', value: 1 },
  { label: '二', value: 2 },
  { label: '三', value: 3 },
  { label: '四', value: 4 },
  { label: '五', value: 5 },
  { label: '六', value: 6 },
  { label: '日', value: 0 }
]

const hourOptions = Array.from({ length: 24 }, (_, i) => ({
  label: `${String(i).padStart(2, '0')}点`,
  value: i
}))

const minuteOptions = Array.from({ length: 60 }, (_, i) => ({
  label: `${String(i).padStart(2, '0')}分`,
  value: i
}))

const dayOfMonthOptions = Array.from({ length: 31 }, (_, i) => ({
  label: `${i + 1}号`,
  value: i + 1
}))

// ============ Helper Algorithms ============

// 1. Cron 表达式校验 (5位)
const validateCron = (cron) => {
  if (!cron) return false
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return false

  const rules = [
    { min: 0, max: 59 }, // 分钟
    { min: 0, max: 23 }, // 小时
    { min: 1, max: 31 }, // 天
    { min: 1, max: 12 }, // 月
    { min: 0, max: 6 } // 周
  ]

  for (let i = 0; i < 5; i++) {
    const part = parts[i]
    if (part === '*') continue

    const subParts = part.split(',')
    for (const sub of subParts) {
      if (!sub) return false
      const rangeReg = /^(\*|\d+)(?:-(\d+))?(?:\/(\d+))?$/
      const match = sub.match(rangeReg)
      if (!match) return false

      const [, startStr, endStr, stepStr] = match
      let minLimit = rules[i].min
      let maxLimit = rules[i].max
      if (i === 4) {
        maxLimit = 7 // 周支持 7
      }

      const start = startStr === '*' ? minLimit : parseInt(startStr, 10)
      const end = endStr ? parseInt(endStr, 10) : startStr === '*' ? maxLimit : start
      const step = stepStr ? parseInt(stepStr, 10) : 1

      if (isNaN(start) || start < minLimit || start > maxLimit) return false
      if (endStr && (isNaN(end) || end < minLimit || end > maxLimit || end < start)) return false
      if (stepStr && (isNaN(step) || step <= 0)) return false
    }
  }
  return true
}

// 2. 智能中文翻译
const translateCronToChinese = (cron) => {
  if (!validateCron(cron)) return '无效的 Cron 表达式'
  const parts = cron.trim().split(/\s+/)
  const [m, h, dom, mon, dow] = parts

  // 纯间隔分钟
  if (m.startsWith('*/') && h === '*' && dom === '*' && mon === '*' && dow === '*') {
    const mins = m.split('/')[1]
    return `每隔 ${mins} 分钟自动执行一次`
  }

  let timeStr
  if (h.includes('*')) {
    if (m === '*') {
      timeStr = '每分钟'
    } else if (m.startsWith('*/')) {
      timeStr = `每小时每隔 ${m.split('/')[1]} 分钟`
    } else {
      timeStr = `每小时的第 ${m} 分钟`
    }
  } else {
    const formatPart = (val, isHour) => {
      if (val.includes(',')) {
        const sorted = val
          .split(',')
          .map(Number)
          .sort((a, b) => a - b)
        return sorted.map((v) => (isHour ? `${v}点` : `${v}分`)).join('和')
      }
      if (val.includes('-')) {
        const [s, e] = val.split('-')
        return `${s}点至${e}点`
      }
      return isHour ? `${String(val).padStart(2, '0')}点` : `${String(val).padStart(2, '0')}分`
    }

    if (h.includes(',') || h.includes('-') || m.includes(',') || m.includes('-')) {
      timeStr = `在 ${formatPart(h, true)} 的 ${formatPart(m, false)}`
    } else {
      timeStr = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
    }
  }

  let dateStr
  if (dom === '*' && dow === '*') {
    dateStr = '每天'
  } else if (dom !== '*' && dow === '*') {
    if (dom.includes(',')) {
      const sortedDays = dom
        .split(',')
        .map(Number)
        .sort((a, b) => a - b)
      dateStr = `每月的 [ ${sortedDays.map((d) => `${d}号`).join(', ')} ]`
    } else if (dom.includes('-')) {
      const [s, e] = dom.split('-')
      dateStr = `每月的 ${s}号至${e}号`
    } else {
      dateStr = `每月的 ${dom} 号`
    }
  } else if (dom === '*' && dow !== '*') {
    const weekMap = {
      1: '周一',
      2: '周二',
      3: '周三',
      4: '周四',
      5: '周五',
      6: '周六',
      0: '周日',
      7: '周日'
    }
    if (dow.includes(',')) {
      const days = dow
        .split(',')
        .map((d) => weekMap[d] || d)
        .join(', ')
      dateStr = `每周的 [ ${days} ]`
    } else if (dow.includes('-')) {
      const [s, e] = dow.split('-')
      dateStr = `每周的 ${weekMap[s] || s} 至 ${weekMap[e] || e}`
    } else {
      dateStr = `每周的 ${weekMap[dow] || dow}`
    }
  } else {
    dateStr = `每月的 ${dom}号 或 每周的 [ ${dow} ]`
  }

  let monStr = ''
  if (mon !== '*') {
    if (mon.includes(',')) {
      monStr = `在 [ ${mon
        .split(',')
        .map((m) => `${m}月`)
        .join(', ')} ] 的`
    } else {
      monStr = `在 ${mon} 月的`
    }
  }

  return `${monStr}${dateStr}的 ${timeStr} 自动执行`
}

// 3. 未来 5 次运行时间预测
const getNextExecutions = (cron, count = 5) => {
  if (!validateCron(cron)) return []
  const parts = cron.trim().split(/\s+/)
  const [mStr, hStr, domStr, monStr, dowStr] = parts

  const parseField = (str, min, max, isDow = false) => {
    const values = new Set()
    if (str === '*') {
      for (let i = min; i <= max; i++) values.add(i)
      return values
    }
    const subParts = str.split(',')
    for (const part of subParts) {
      const match = part.match(/^(\*|\d+)(?:-(\d+))?(?:\/(\d+))?$/)
      if (!match) continue
      const [, startS, endS, stepS] = match
      const start = startS === '*' ? min : parseInt(startS, 10)
      const end = endS ? parseInt(endS, 10) : startS === '*' ? max : start
      const step = stepS ? parseInt(stepS, 10) : 1
      for (let i = start; i <= end; i += step) {
        let val = i
        if (isDow && val === 7) val = 0
        values.add(val)
      }
    }
    return values
  }

  const minSet = parseField(mStr, 0, 59)
  const hourSet = parseField(hStr, 0, 23)
  const domSet = parseField(domStr, 1, 31)
  const monSet = parseField(monStr, 1, 12)
  const dowSet = parseField(dowStr, 0, 6, true)

  const results = []
  const iterDate = new Date()
  iterDate.setSeconds(0)
  iterDate.setMilliseconds(0)

  const maxIterations = 525600
  let iterations = 0

  const isDomRestricted = domStr !== '*'
  const isDowRestricted = dowStr !== '*'

  while (results.length < count && iterations < maxIterations) {
    iterations++
    iterDate.setMinutes(iterDate.getMinutes() + 1)

    const curMin = iterDate.getMinutes()
    const curHour = iterDate.getHours()
    const curDom = iterDate.getDate()
    const curMon = iterDate.getMonth() + 1
    const curDow = iterDate.getDay()

    if (!minSet.has(curMin)) continue
    if (!hourSet.has(curHour)) continue
    if (!monSet.has(curMon)) continue

    if (isDomRestricted && isDowRestricted) {
      if (!domSet.has(curDom) && !dowSet.has(curDow)) continue
    } else {
      if (isDomRestricted && !domSet.has(curDom)) continue
      if (isDowRestricted && !dowSet.has(curDow)) continue
    }

    results.push(new Date(iterDate))
  }

  return results
}

// ============ Computed ============
const isTempCronValid = computed(() => {
  return validateCron(tempCronExpr.value)
})

const translateText = computed(() => {
  return translateCronToChinese(tempCronExpr.value)
})

const nextExecutions = computed(() => {
  return getNextExecutions(tempCronExpr.value, 5)
})

const displayValue = computed(() => {
  if (!props.value || !validateCron(props.value)) return props.value || '未配置周期'
  const chineseText = translateCronToChinese(props.value)
  return `${props.value} (${chineseText})`
})

// ============ Actions ============
const toggleWeekDay = (val) => {
  const current = [...cronDayOfWeek.value]
  const idx = current.indexOf(val)
  if (idx > -1) {
    cronDayOfWeek.value = current.filter((v) => v !== val)
  } else {
    cronDayOfWeek.value = [...current, val]
  }
}

const generateCron = () => {
  if (isAdvancedMode.value) return

  let m
  let h
  let dom = '*'
  let mon = '*'
  let dow = '*'

  if (quickType.value === 'interval') {
    m = `*/${cronIntervalMins.value}`
    h = '*'
  } else {
    m = String(cronMinute.value)
    h = String(cronHour.value)

    if (quickType.value === 'weekly') {
      if (cronDayOfWeek.value && cronDayOfWeek.value.length > 0) {
        if (cronDayOfWeek.value.length === 7) {
          dow = '*'
        } else {
          const sorted = [...cronDayOfWeek.value].sort((a, b) => a - b)
          dow = sorted.join(',')
        }
      } else {
        dow = '*'
      }
    } else if (quickType.value === 'monthly') {
      dom = String(cronDayOfMonth.value)
    }
  }

  tempCronExpr.value = `${m} ${h} ${dom} ${mon} ${dow}`
}

const parseCronToConfig = (cron) => {
  if (!cron || !validateCron(cron)) return
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) {
    isAdvancedMode.value = true
    return
  }

  const [m, h, dom, mon, dow] = parts

  // 1. 间隔分钟
  if (m.startsWith('*/') && h === '*' && dom === '*' && mon === '*' && dow === '*') {
    const mins = parseInt(m.substring(2), 10)
    if (!isNaN(mins)) {
      isAdvancedMode.value = false
      quickType.value = 'interval'
      cronIntervalMins.value = mins
      return
    }
  }

  // 2. 每天
  if (
    !m.includes('*') &&
    !m.includes('/') &&
    !m.includes(',') &&
    !m.includes('-') &&
    !h.includes('*') &&
    !h.includes('/') &&
    !h.includes(',') &&
    !h.includes('-') &&
    dom === '*' &&
    mon === '*' &&
    dow === '*'
  ) {
    const minVal = parseInt(m, 10)
    const hrVal = parseInt(h, 10)
    if (!isNaN(minVal) && !isNaN(hrVal)) {
      isAdvancedMode.value = false
      quickType.value = 'daily'
      cronHour.value = hrVal
      cronMinute.value = minVal
      return
    }
  }

  // 3. 按周
  if (
    !m.includes('*') &&
    !m.includes('/') &&
    !m.includes(',') &&
    !m.includes('-') &&
    !h.includes('*') &&
    !h.includes('/') &&
    !h.includes(',') &&
    !h.includes('-') &&
    dom === '*' &&
    mon === '*' &&
    dow !== '*'
  ) {
    const minVal = parseInt(m, 10)
    const hrVal = parseInt(h, 10)
    if (!isNaN(minVal) && !isNaN(hrVal)) {
      isAdvancedMode.value = false
      quickType.value = 'weekly'
      cronHour.value = hrVal
      cronMinute.value = minVal

      const weekDays = []
      if (dow.includes(',')) {
        dow.split(',').forEach((d) => {
          const v = parseInt(d, 10)
          if (!isNaN(v)) weekDays.push(v === 7 ? 0 : v)
        })
      } else if (dow.includes('-')) {
        const [start, end] = dow.split('-').map(Number)
        if (!isNaN(start) && !isNaN(end)) {
          for (let i = start; i <= end; i++) {
            weekDays.push(i === 7 ? 0 : i)
          }
        }
      } else {
        const v = parseInt(dow, 10)
        if (!isNaN(v)) weekDays.push(v === 7 ? 0 : v)
      }
      cronDayOfWeek.value = weekDays.sort((a, b) => a - b)
      return
    }
  }

  // 4. 按月
  if (
    !m.includes('*') &&
    !m.includes('/') &&
    !m.includes(',') &&
    !m.includes('-') &&
    !h.includes('*') &&
    !h.includes('/') &&
    !h.includes(',') &&
    !h.includes('-') &&
    dom !== '*' &&
    !dom.includes(',') &&
    !dom.includes('-') &&
    !dom.includes('/') &&
    mon === '*' &&
    dow === '*'
  ) {
    const minVal = parseInt(m, 10)
    const hrVal = parseInt(h, 10)
    const domVal = parseInt(dom, 10)
    if (!isNaN(minVal) && !isNaN(hrVal) && !isNaN(domVal)) {
      isAdvancedMode.value = false
      quickType.value = 'monthly'
      cronHour.value = hrVal
      cronMinute.value = minVal
      cronDayOfMonth.value = domVal
      return
    }
  }

  isAdvancedMode.value = true
}

// 监听配置变量变化重新生成 cron
watch(
  [
    isAdvancedMode,
    quickType,
    cronIntervalMins,
    cronHour,
    cronMinute,
    cronDayOfWeek,
    cronDayOfMonth
  ],
  () => {
    generateCron()
  },
  { deep: true }
)

// 监听外层传入的 value
watch(
  () => props.value,
  (newVal) => {
    tempCronExpr.value = newVal
    parseCronToConfig(newVal)
  },
  { immediate: true }
)

const handleConfirm = () => {
  if (!isTempCronValid.value) return
  emit('update:value', tempCronExpr.value)
  visible.value = false
}

const handleCancel = () => {
  tempCronExpr.value = props.value
  parseCronToConfig(props.value)
  visible.value = false
}

const formatPredictionDate = (date) => {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  const ss = String(date.getSeconds()).padStart(2, '0')
  return `${y}-${m}-${d} ${hh}:${mm}:${ss}`
}
</script>

<template>
  <div class="cron-selector-wrapper">
    <a-popover
      v-model:open="visible"
      trigger="click"
      placement="bottomLeft"
      :overlay-style="{ width: '640px' }"
      :overlay-inner-style="{ padding: '0px', borderRadius: '16px', overflow: 'hidden' }"
      :getPopupContainer="(triggerNode) => triggerNode.closest('.ant-drawer-body') || triggerNode.closest('.ant-modal-body') || triggerNode.parentNode"
      destroy-on-close
    >
      <a-input
        :value="displayValue"
        readonly
        placeholder="点击配置定时任务的执行周期..."
        class="trigger-input"
      >
        <template #suffix>
          <Clock :size="14" class="text-muted clock-icon" />
        </template>
      </a-input>

      <template #content>
        <div class="cron-editor-panel">
          <!-- 顶部大药丸模式切换 Toggle -->
          <div class="panel-header">
            <div class="mode-toggle-wrapper">
              <div
                class="mode-toggle-btn"
                :class="{ active: !isAdvancedMode }"
                @click="isAdvancedMode = false"
              >
                快捷配置
              </div>
              <div
                class="mode-toggle-btn"
                :class="{ active: isAdvancedMode }"
                @click="isAdvancedMode = true"
              >
                高级自定义 (Cron)
              </div>
            </div>
          </div>

          <!-- 中间分栏布局 -->
          <div class="panel-body">
            <!-- 左栏：未来 5 次执行时间预测 -->
            <div class="left-column">
              <div class="forecast-header">
                <Calendar :size="14" class="forecast-icon" />
                <span>未来 5 次执行预测</span>
              </div>
              <div class="forecast-list" v-if="isTempCronValid && nextExecutions.length > 0">
                <div
                  v-for="(date, index) in nextExecutions"
                  :key="index"
                  class="forecast-item"
                  :class="{ first: index === 0 }"
                >
                  <span class="forecast-dot"></span>
                  <span class="forecast-time">{{ formatPredictionDate(date) }}</span>
                </div>
              </div>
              <div class="forecast-empty" v-else>
                <AlertCircle :size="14" class="text-danger" />
                <span>暂无有效的执行预测</span>
              </div>
            </div>

            <!-- 右栏：配置选项及翻译 -->
            <div class="right-column">
              <!-- A. 快捷配置面板 -->
              <div v-if="!isAdvancedMode" class="quick-config-container">
                <div class="right-top-section">
                  <div class="frequency-title">执行频率</div>
                  <a-radio-group v-model:value="quickType" size="small" class="quick-type-radios">
                    <a-radio-button value="interval">按间隔</a-radio-button>
                    <a-radio-button value="daily">按天</a-radio-button>
                    <a-radio-button value="weekly">按周</a-radio-button>
                    <a-radio-button value="monthly">按月</a-radio-button>
                  </a-radio-group>
                </div>

                <!-- 二级配置项 -->
                <div class="quick-details-wrapper">
                  <!-- 间隔 -->
                  <div
                    v-if="quickType === 'interval'"
                    class="setting-detail-row flex-column align-start"
                  >
                    <div class="setting-card">
                      <span class="card-label">运行时间间隔</span>
                      <div class="flex-align-center gap-6" style="height: 34px">
                        <span>每隔</span>
                        <a-select
                          v-model:value="cronIntervalMins"
                          size="small"
                          class="styled-select"
                          style="width: 80px"
                        >
                          <a-select-option :value="1">1</a-select-option>
                          <a-select-option :value="5">5</a-select-option>
                          <a-select-option :value="10">10</a-select-option>
                          <a-select-option :value="15">15</a-select-option>
                          <a-select-option :value="30">30</a-select-option>
                          <a-select-option :value="60">60</a-select-option>
                        </a-select>
                        <span>分钟自动执行一次</span>
                      </div>
                      <span class="card-tip">高频自动任务，适用于即时性监控或定时同步场景</span>
                    </div>
                  </div>

                  <!-- 每天 -->
                  <div
                    v-else-if="quickType === 'daily'"
                    class="setting-detail-row flex-column align-start"
                  >
                    <div class="setting-card">
                      <span class="card-label">运行时间点</span>
                      <div class="time-picker-simulate">
                        <a-select
                          v-model:value="cronHour"
                          :options="hourOptions"
                          size="small"
                          class="styled-select"
                          style="width: 65px"
                        />
                        <span class="colon">:</span>
                        <a-select
                          v-model:value="cronMinute"
                          :options="minuteOptions"
                          size="small"
                          class="styled-select"
                          style="width: 65px"
                        />
                        <Clock :size="12" class="text-muted" style="margin-left: 6px" />
                      </div>
                      <span class="card-tip">每天将在设定的固定时刻在后台自动触发运行</span>
                    </div>
                  </div>

                  <!-- 按周 -->
                  <div
                    v-else-if="quickType === 'weekly'"
                    class="setting-detail-row flex-column align-start"
                  >
                    <div class="setting-card">
                      <span class="card-label">运行星期</span>
                      <div class="week-badges-container">
                        <div
                          v-for="opt in weekBadgeOptions"
                          :key="opt.value"
                          class="week-badge-item"
                          :class="{ active: cronDayOfWeek.includes(opt.value) }"
                          @click="toggleWeekDay(opt.value)"
                        >
                          {{ opt.label }}
                        </div>
                      </div>
                      <div class="start-time-row" style="margin-top: 8px">
                        <span class="label">开始时间</span>
                        <div class="time-picker-simulate">
                          <a-select
                            v-model:value="cronHour"
                            :options="hourOptions"
                            size="small"
                            class="styled-select"
                            style="width: 65px"
                          />
                          <span class="colon">:</span>
                          <a-select
                            v-model:value="cronMinute"
                            :options="minuteOptions"
                            size="small"
                            class="styled-select"
                            style="width: 65px"
                          />
                          <Clock :size="12" class="text-muted" style="margin-left: 6px" />
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- 按月 (恢复为极简、直观的单天选择) -->
                  <div
                    v-else-if="quickType === 'monthly'"
                    class="setting-detail-row flex-column align-start"
                  >
                    <div class="setting-card">
                      <span class="card-label">运行时间点</span>
                      <div class="flex-align-center gap-6" style="height: 34px; box-sizing: border-box">
                        <span>每月的</span>
                        <a-select
                          v-model:value="cronDayOfMonth"
                          :options="dayOfMonthOptions"
                          size="small"
                          class="styled-select"
                          style="width: 80px"
                        />
                        <span>自动触发</span>
                      </div>
                      <div class="start-time-row" style="margin-top: 8px">
                        <span class="label">开始时间</span>
                        <div class="time-picker-simulate">
                          <a-select
                            v-model:value="cronHour"
                            :options="hourOptions"
                            size="small"
                            class="styled-select"
                            style="width: 65px"
                          />
                          <span class="colon">:</span>
                          <a-select
                            v-model:value="cronMinute"
                            :options="minuteOptions"
                            size="small"
                            class="styled-select"
                            style="width: 65px"
                          />
                          <Clock :size="12" class="text-muted" style="margin-left: 6px" />
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 实时中文翻译卡片 -->
                <div class="translation-card">
                  <div class="flex-align-center gap-6 text-translation">
                    <Info :size="14" class="info-icon" />
                    <span class="translate-text">{{ translateText }}</span>
                  </div>
                </div>
              </div>

              <!-- B. 高级自定义面板 -->
              <div v-else class="advanced-config-container">
                <div class="right-top-section">
                  <div class="advanced-title">高级自定义 (Cron)</div>
                  <!-- 极客深色代码风输入框 -->
                  <div class="code-editor-box">
                    <div class="editor-input-row">
                      <input
                        v-model="tempCronExpr"
                        placeholder="* * * * *"
                        class="editor-raw-input"
                      />
                      <div class="validation-status">
                        <span v-if="isTempCronValid" class="valid-badge">
                          <Check :size="12" class="icon" /> 验证通过
                        </span>
                        <span v-else class="invalid-badge">
                          <AlertCircle :size="12" class="icon" /> 格式错误
                        </span>
                      </div>
                    </div>
                  </div>
                  <span class="text-muted format-tip">五位标准格式: 分 时 日 月 周 (空格分隔)</span>
                </div>

                <!-- 翻译提示 -->
                <div
                  class="translation-card code-translation"
                  :class="{ invalid: !isTempCronValid }"
                >
                  <div class="flex-align-center gap-6 text-translation">
                    <Info :size="14" class="info-icon" />
                    <span class="translate-text">{{ translateText }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 底部控制按钮 -->
          <div class="panel-footer">
            <a-button size="small" class="cancel-btn" @click="handleCancel">取消</a-button>
            <a-button
              size="small"
              type="primary"
              class="confirm-btn"
              :disabled="!isTempCronValid"
              @click="handleConfirm"
            >
              保存周期
            </a-button>
          </div>
        </div>
      </template>
    </a-popover>
  </div>
</template>

<style scoped lang="less">
.cron-selector-wrapper {
  width: 100%;
  position: relative;

  .trigger-input {
    cursor: pointer;
    background-color: var(--gray-0);
    border-radius: 8px;
    height: 38px;

    :deep(input) {
      cursor: pointer;
      text-overflow: ellipsis;
      white-space: nowrap;
      overflow: hidden;
      font-size: 13px;
    }
  }

  .clock-icon {
    color: var(--gray-400);
    transition: color 0.2s ease;
  }

  &:hover .clock-icon {
    color: var(--main-color);
  }
}

// ============ Popover 内部玻璃态面板 ============
.cron-editor-panel {
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.5);
  width: 640px;
  box-sizing: border-box;

  // 顶部药丸 Toggle
  .panel-header {
    padding: 16px 16px 10px;
    display: flex;
    justify-content: center;
    border-bottom: 1px solid rgba(0, 0, 0, 0.05);

    .mode-toggle-wrapper {
      display: flex;
      background-color: rgba(0, 0, 0, 0.04);
      padding: 3px;
      border-radius: 20px;
      width: 100%;
      box-sizing: border-box;

      .mode-toggle-btn {
        flex: 1;
        text-align: center;
        padding: 6px 0;
        font-size: 12px;
        font-weight: 600;
        color: var(--gray-600);
        cursor: pointer;
        border-radius: 17px;
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        user-select: none;

        &:hover {
          color: var(--gray-800);
        }

        &.active {
          background-color: var(--gray-0);
          color: var(--main-color);
          box-shadow:
            0 4px 6px -1px rgba(0, 0, 0, 0.05),
            0 2px 4px -1px rgba(0, 0, 0, 0.03);
        }
      }
    }
  }

  // 中间分栏布局
  .panel-body {
    display: flex;
    min-height: 180px;
    height: auto;
    border-bottom: 1px solid rgba(0, 0, 0, 0.05);

    // 左栏：Next 5 Executions 预测
    .left-column {
      width: 42%;
      border-right: 1px solid rgba(0, 0, 0, 0.05);
      padding: 16px;
      display: flex;
      flex-direction: column;
      background-color: rgba(0, 0, 0, 0.01);
      box-sizing: border-box;

      .forecast-header {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        font-weight: 700;
        color: var(--gray-800);
        margin-bottom: 12px;

        .forecast-icon {
          color: var(--main-color);
        }
      }

      .forecast-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
        flex: 1;
        overflow-y: auto;
        padding-bottom: 8px;
      }

      .forecast-item {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 11px;
        color: var(--gray-600);
        padding: 4px 0;
        transition: all 0.2s ease;

        .forecast-dot {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          background-color: var(--gray-300);
        }

        .forecast-time {
          font-family: monospace;
          font-weight: 500;
        }

        &.first {
          color: var(--main-color);

          .forecast-dot {
            background-color: var(--main-color);
            box-shadow: 0 0 0 3px color-mix(in srgb, var(--main-color) 20%, transparent);
          }

          .forecast-time {
            font-weight: 600;
          }
        }
      }

      .forecast-empty {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 11px;
        color: var(--gray-400);
        padding: 12px 0;
      }
    }

    // 右栏：配置选项及翻译
    .right-column {
      width: 58%;
      padding: 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      box-sizing: border-box;

      .quick-config-container,
      .advanced-config-container {
        display: flex;
        flex-direction: column;
        height: 100%;
        justify-content: space-between;
        gap: 12px;
      }

      .right-top-section {
        display: flex;
        flex-direction: column;
      }

      .frequency-title,
      .advanced-title {
        font-size: 12px;
        font-weight: 700;
        color: var(--gray-800);
        margin-bottom: 8px;
      }

      .quick-type-radios {
        margin-bottom: 12px;
        width: 100%;
        display: flex;

        :deep(.ant-radio-button-wrapper) {
          flex: 1;
          text-align: center;
          font-size: 11px;
          height: 28px;
          line-height: 26px;
        }
      }

      // 二级配置项包装容器
      .quick-details-wrapper {
        flex: 1;
        display: flex;
        flex-direction: column;
        justify-content: center;

        .setting-detail-row {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 12px;
          color: var(--gray-700);
          width: 100%;

          &.align-start {
            align-items: flex-start;
          }
        }

        .styled-select {
          :deep(.ant-select-selector) {
            border-radius: 6px;
            height: 28px;
            font-size: 12px;
            display: flex;
            align-items: center;
          }
        }

        .setting-card {
          width: 100%;
          background-color: rgba(0, 0, 0, 0.02);
          border: 1px solid var(--gray-200);
          border-radius: 8px;
          padding: 10px 12px;
          display: flex;
          flex-direction: column;
          gap: 8px;

          .card-label {
            font-size: 10px;
            font-weight: bold;
            color: var(--gray-400);
            text-transform: uppercase;
            letter-spacing: 0.5px;
          }

          .card-tip {
            font-size: 11px;
            color: var(--gray-400);
            margin-top: 2px;
          }
        }

        .time-picker-simulate {
          display: flex;
          align-items: center;
          gap: 4px;
          background-color: var(--gray-0);
          padding: 2px 6px;
          border-radius: 8px;
          border: 1px solid var(--gray-200);
          align-self: flex-start;
          height: 34px;
          box-sizing: border-box;

          .colon {
            font-weight: bold;
            color: var(--gray-500);
          }
        }

        .start-time-row {
          display: flex;
          align-items: center;
          gap: 8px;

          .label {
            font-weight: 600;
            color: var(--gray-500);
            font-size: 11px;
            text-transform: uppercase;
          }
        }

        // 星期圆形徽章
        .week-badges-container {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
          height: 34px;
          align-items: center;
          box-sizing: border-box;

          .week-badge-item {
            width: 26px;
            height: 26px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            background-color: var(--gray-50);
            border: 1px solid var(--gray-200);
            font-size: 11px;
            font-weight: 600;
            color: var(--gray-600);
            cursor: pointer;
            transition: all 0.2s ease;
            user-select: none;

            &:hover {
              border-color: var(--main-color);
              color: var(--main-color);
            }

            &.active {
              background-color: var(--main-color);
              border-color: var(--main-color);
              color: var(--gray-0);
              box-shadow: 0 4px 6px -1px color-mix(in srgb, var(--main-color) 25%, transparent);
            }
          }
        }
      }

      // 中文翻译卡片
      .translation-card {
        background-color: color-mix(in srgb, var(--main-color) 6%, var(--gray-0));
        border-radius: 8px;
        padding: 8px 12px;
        border: 1px solid color-mix(in srgb, var(--main-color) 12%, var(--gray-0));

        .text-translation {
          font-size: 11px;
          color: var(--main-color);
          font-weight: 600;
          line-height: 1.4;

          .info-icon {
            flex-shrink: 0;
          }
        }

        &.code-translation {
          margin-top: 10px;

          &.invalid {
            background-color: #fff1f0;
            border-color: #ffccc7;

            .text-translation {
              color: #ff4d4f;
            }
          }
        }
      }

      // 高级模式输入框样式
      .editor-input-row {
        position: relative;
        display: flex;
        align-items: center;
        background-color: #1e1e2e;
        border-radius: 8px;
        padding: 8px 12px;
        border: 1px solid rgba(255, 255, 255, 0.05);

        .editor-raw-input {
          flex: 1;
          background: transparent;
          border: none;
          outline: none;
          color: #f5e0dc;
          font-family: monospace;
          font-size: 13px;
          font-weight: 600;
          letter-spacing: 0.5px;
        }

        .validation-status {
          display: flex;
          align-items: center;

          .valid-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 10px;
            font-weight: bold;
            color: #52c41a;
            background-color: rgba(82, 196, 26, 0.15);
            padding: 2px 6px;
            border-radius: 4px;
          }

          .invalid-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 10px;
            font-weight: bold;
            color: #ff4d4f;
            background-color: rgba(255, 77, 79, 0.15);
            padding: 2px 6px;
            border-radius: 4px;
          }
        }
      }

      .format-tip {
        font-size: 11px;
        margin-top: 4px;
        display: block;
      }
    }
  }

  // 底部控制
  .panel-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    background-color: rgba(0, 0, 0, 0.02);

    .cancel-btn {
      border-radius: 6px;
      font-size: 12px;
    }

    .confirm-btn {
      border-radius: 6px;
      font-size: 12px;
    }
  }
}

// 公用 Flex 辅助类
.flex-align-center {
  display: flex;
  align-items: center;
}

.flex-column {
  display: flex;
  flex-direction: column;
}

.align-start {
  align-items: flex-start;
}

.gap-6 {
  gap: 6px;
}

.gap-8 {
  gap: 8px;
}

.text-danger {
  color: #ff4d4f;
}
</style>
