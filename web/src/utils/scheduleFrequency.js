export const scheduleFrequencies = [
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周' },
  { value: 'monthly', label: '每月' },
  { value: 'yearly', label: '每年' },
  { value: 'custom', label: '自定义' }
]

export const weekdayOptions = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 0, label: '日' }
]

export const monthOptions = Array.from({ length: 12 }, (_, index) => ({
  value: index + 1,
  label: `${index + 1} 月`
}))

export const dayOptions = Array.from({ length: 31 }, (_, index) => ({
  value: index + 1,
  label: `${index + 1} 号`
}))

const pad = (value) => String(value).padStart(2, '0')

const normalizeTime = (time) => {
  const match = String(time || '').match(/^(\d{1,2}):(\d{1,2})$/)
  if (!match) return ['0', '9']
  return [String(Number(match[2])), String(Number(match[1]))]
}

const expandWeekdays = (value) => {
  const result = new Set()
  for (const part of String(value || '').split(',')) {
    const range = part.match(/^(\d+)-(\d+)$/)
    if (range) {
      for (let day = Number(range[1]); day <= Number(range[2]); day += 1) result.add(day % 7)
    } else if (/^\d+$/.test(part)) {
      result.add(Number(part) % 7)
    }
  }
  return [...result].sort((a, b) => a - b)
}

export const buildCronExpression = (schedule) => {
  const { frequency, time, weekdays, dayOfMonth, month, cronExpression } = schedule
  if (frequency === 'custom') return String(cronExpression || '').trim()
  const [minute, hour] = normalizeTime(time)
  if (frequency === 'weekly') {
    const selected = [...new Set(weekdays)].sort((a, b) => a - b)
    return `${minute} ${hour} * * ${selected.length ? selected.join(',') : '1'}`
  }
  if (frequency === 'monthly') return `${minute} ${hour} ${dayOfMonth || 1} * *`
  if (frequency === 'yearly') return `${minute} ${hour} ${dayOfMonth || 1} ${month || 1} *`
  return `${minute} ${hour} * * *`
}

/** 切换频率，并在进入自定义模式前保存当前结构化计划。 */
export const applyFrequencyChange = (schedule, frequency) => ({
  ...schedule,
  cronExpression:
    frequency === 'custom' && schedule.frequency !== 'custom'
      ? buildCronExpression(schedule)
      : schedule.cronExpression,
  frequency
})

export const parseCronExpression = (expression) => {
  const cronExpression = String(expression || '').trim()
  const parts = cronExpression.split(/\s+/)
  const [minute, hour, day = '*', month = '*', weekday = '*'] = parts
  const numericTime = /^\d+$/.test(minute) && /^\d+$/.test(hour)
  const minuteNumber = Number(minute)
  const hourNumber = Number(hour)
  const timeSupported =
    numericTime && minuteNumber >= 0 && minuteNumber <= 59 && hourNumber >= 0 && hourNumber <= 23
  const time = timeSupported ? `${pad(hour)}:${pad(minute)}` : '09:00'
  const simpleDay = /^([1-9]|[12]\d|3[01])$/.test(day)
  const simpleMonth = /^([1-9]|1[0-2])$/.test(month)
  const simpleWeekday = /^(?:[0-7](?:-[0-7])?)(?:,[0-7](?:-[0-7])?)*$/.test(weekday)
  const result = { cronExpression, time, weekdays: [1], dayOfMonth: 1, month: 1 }

  if (parts.length !== 5 || !timeSupported) {
    return { ...result, frequency: 'custom' }
  }
  if (day === '*' && month === '*' && simpleWeekday) {
    const weekdays = expandWeekdays(weekday)
    if (weekdays.length) {
      return { ...result, frequency: 'weekly', weekdays }
    }
  }
  if (simpleDay && simpleMonth && weekday === '*') {
    return {
      ...result,
      frequency: 'yearly',
      dayOfMonth: Number(day) || 1,
      month: Number(month) || 1
    }
  }
  if (simpleDay && month === '*' && weekday === '*') {
    return {
      ...result,
      frequency: 'monthly',
      dayOfMonth: Number(day) || 1
    }
  }
  if (day === '*' && month === '*' && weekday === '*') {
    return { ...result, frequency: 'daily' }
  }
  return { ...result, frequency: 'custom' }
}

export const describeSchedule = (schedule) => {
  const { frequency, time, weekdays, dayOfMonth, month, cronExpression } = schedule
  if (frequency === 'custom') return `Cron ${cronExpression}`
  const displayTime = time || '09:00'
  if (frequency === 'weekly') {
    const selected = [...new Set(weekdays)].sort((a, b) => a - b)
    const isWorkday = selected.join(',') === '1,2,3,4,5'
    const labels = selected
      .map((day) => weekdayOptions.find((option) => option.value === day)?.label)
      .filter(Boolean)
    return `${isWorkday ? '工作日' : `每周${labels.join('、') || '一'}`} ${displayTime}`
  }
  if (frequency === 'monthly') return `每月 ${dayOfMonth || 1} 号 ${displayTime}`
  if (frequency === 'yearly') return `每年 ${month || 1} 月 ${dayOfMonth || 1} 号 ${displayTime}`
  return `每天 ${displayTime}`
}
