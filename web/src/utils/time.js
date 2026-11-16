import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn.js'
import utc from 'dayjs/plugin/utc.js'
import timezone from 'dayjs/plugin/timezone.js'
import relativeTime from 'dayjs/plugin/relativeTime.js'

dayjs.extend(utc)
dayjs.extend(timezone)
dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

const DEFAULT_TZ = 'Asia/Shanghai'
dayjs.tz.setDefault(DEFAULT_TZ)

const NUMERIC_REGEX = /^-?\d+(?:\.\d+)?$/

const coerceDayjs = (value) => {
  if (value === null || value === undefined) {
    return null
  }

  if (typeof value === 'number') {
    return dayjs(value).tz(DEFAULT_TZ)
  }

  const stringValue = String(value).trim()
  if (!stringValue) {
    return null
  }

  if (NUMERIC_REGEX.test(stringValue)) {
    const numeric = Number(stringValue)
    if (Number.isNaN(numeric)) {
      return null
    }

    // 值小于 10^12 时视为秒级时间戳，否则视为毫秒
    if (Math.abs(numeric) < 1e12) {
      return dayjs.unix(numeric).tz(DEFAULT_TZ)
    }
    return dayjs(numeric).tz(DEFAULT_TZ)
  }

  // 解析 ISO 字符串（dayjs 会自动识别时区信息，如 Z 后缀表示 UTC）
  // 需要先转换为 UTC 再设置时区，否则 .tz() 只会改变显示而不会正确转换
  const parsed = dayjs(stringValue)
  if (!parsed.isValid()) {
    return null
  }
  // 先转换为 UTC（保留原始时间值），再转换到上海时区
  return parsed.utc().tz(DEFAULT_TZ)
}

export const parseToShanghai = (value) => coerceDayjs(value)

export const formatDateTime = (value, format = 'YYYY-MM-DD HH:mm') => {
  const parsed = coerceDayjs(value)
  if (!parsed) return '-'
  return parsed.format(format)
}

export const formatFullDateTime = (value) => formatDateTime(value, 'YYYY-MM-DD HH:mm:ss')

export const formatRelative = (value) => {
  const parsed = coerceDayjs(value)
  if (!parsed) return '-'
  return parsed.fromNow()
}

// 对话时间展示：今天仅时间，昨天带"昨天"前缀，一周内显示周几，
// 一周前显示月-日与时间，跨年补全年份；nowValue 供测试注入当前时间
export const formatChatTime = (value, nowValue = undefined) => {
  const parsed = coerceDayjs(value)
  if (!parsed) return ''
  const now = coerceDayjs(nowValue ?? Date.now())

  if (parsed.isSame(now, 'day')) return parsed.format('HH:mm')
  if (parsed.isSame(now.subtract(1, 'day'), 'day')) return parsed.format('昨天 HH:mm')
  if (parsed.isAfter(now.subtract(7, 'day'))) return parsed.format('ddd HH:mm')
  if (parsed.isSame(now, 'year')) return parsed.format('MM-DD HH:mm')
  return parsed.format('YYYY-MM-DD')
}

export default dayjs
