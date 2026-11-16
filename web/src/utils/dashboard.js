/** 格式化 Dashboard 指标数字。 */
export const formatNumber = (value) => Number(value || 0).toLocaleString()

/** 将字节数压缩为最多四位有效数字，并单独返回容量单位。 */
export function formatStorageSize(bytes) {
  let value = Math.max(Number(bytes || 0), 0)
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
  let unitIndex = 0

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }

  return {
    value: Number(value.toPrecision(4)).toString(),
    unit: units[unitIndex]
  }
}

/** 按热力图周列生成月份分段，并忽略不足两周的首尾残月。 */
export function buildHeatmapMonthSegments(weeks) {
  const segments = []
  weeks.forEach((week, index) => {
    const firstCell = week.find(Boolean)
    if (!firstCell) return
    const label = `${Number(firstCell.date.slice(5, 7))}月`
    const currentSegment = segments[segments.length - 1]
    if (currentSegment?.label === label) {
      currentSegment.span += 1
      return
    }
    segments.push({ key: `${label}-${index}`, label, start: index, span: 1 })
  })
  return segments.filter((month) => month.span >= 2)
}
