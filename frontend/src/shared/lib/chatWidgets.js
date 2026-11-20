const MAX_SOURCE_LENGTH = 24000
const MAX_NODES = 100
const MAX_DEPTH = 8
const containerTypes = new Set([
  'Card',
  'ListView',
  'ListViewItem',
  'Row',
  'Col',
  'ImageGroup',
  'Form'
])
const textTypes = new Set(['Title', 'Text', 'Caption'])
const fieldTypes = new Set(['Input', 'Textarea', 'RadioGroup', 'DatePicker'])
const fieldProperties = ['name', 'label', 'defaultValue', 'required', 'disabled']
const layoutProperties = ['gap', 'padding', 'align', 'justify', 'flex']
const textProperties = ['size', 'weight', 'textAlign', 'color']
const WIDGET_ICON_NAMES = [
  'book-open',
  'document',
  'lightbulb',
  'info',
  'check',
  'plus',
  'search',
  'chevron-right'
]
const properties = {
  Card: ['children', 'size', 'padding'],
  ListView: ['children'],
  ListViewItem: ['children', 'onClickAction', 'gap', 'align'],
  Row: ['children', ...layoutProperties],
  Col: ['children', ...layoutProperties],
  Form: ['children', 'onSubmitAction', ...layoutProperties],
  Button: [
    'label',
    'submit',
    'onClickAction',
    'style',
    'variant',
    'size',
    'pill',
    'block',
    'disabled',
    'iconStart',
    'iconEnd'
  ],
  Input: [...fieldProperties, 'placeholder'],
  Textarea: [...fieldProperties, 'placeholder', 'rows'],
  RadioGroup: [...fieldProperties, 'options'],
  DatePicker: [...fieldProperties, 'min', 'max'],
  Title: ['value', ...textProperties],
  Text: ['value', ...textProperties],
  Caption: ['value', ...textProperties],
  Badge: ['label', 'color', 'variant', 'size', 'pill'],
  Icon: ['name', 'size', 'color'],
  Chart: ['label', 'data', 'series', 'xAxis', 'showLegend', 'height'],
  Image: ['src', 'path', 'alt', 'caption', 'fit', 'aspectRatio'],
  ImageGroup: ['children'],
  Divider: [],
  Spacer: []
}

/** 校验模型输出的有限组件树，拒绝脚本、任意属性与未知动作。 */
export function parseWidget(source) {
  if (source.length > MAX_SOURCE_LENGTH) throw new Error('组件 JSON 超过 24000 字符')
  const root = JSON.parse(source)
  if (!['Card', 'ListView', 'Image', 'ImageGroup', 'Form'].includes(root?.type))
    throw new Error('根组件必须是 Card、ListView、Image、ImageGroup 或 Form')
  let count = 0
  const isTextValue = (value) => typeof value === 'string' && value.length <= 4000
  const isComposeAction = (value) =>
    value &&
    value.type === 'compose' &&
    isTextValue(value.text) &&
    value.text.trim() &&
    Object.keys(value).every((key) => ['type', 'text'].includes(key))
  const isSendAction = (value) =>
    value?.type === 'send_message' &&
    Object.keys(value).every((key) => ['type', 'payload'].includes(key)) &&
    value.payload &&
    Object.keys(value.payload).length === 1 &&
    isTextValue(value.payload.text) &&
    Boolean(value.payload.text.trim())
  const isDate = (value) =>
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 10) === value

  /** 在渲染边界检查节点类型和属性。 */
  function visit(node, depth, inActionItem = false, form = null) {
    if (++count > MAX_NODES || depth > MAX_DEPTH)
      throw new Error('组件树超过 100 个节点或 8 层深度')
    const allowed = Object.hasOwn(properties, node?.type) ? properties[node.type] : null
    if (!allowed || Object.keys(node).some((key) => key !== 'type' && !allowed.includes(key))) {
      throw new Error('组件类型或属性不受支持')
    }
    if (
      inActionItem &&
      (['Chart', 'Form', 'Button'].includes(node.type) ||
        fieldTypes.has(node.type) ||
        node.onClickAction)
    )
      throw new Error('可点击列表项不能包含交互控件')
    if (node.type === 'Form') {
      if (form) throw new Error('表单不能嵌套')
      if (!isSendAction(node.onSubmitAction))
        throw new Error('Form 只支持 send_message 动作和 payload.text')
      form = { names: new Set(), hasSubmit: false }
    }
    if (fieldTypes.has(node.type)) {
      if (!form) throw new Error('输入组件必须放在 Form 内')
      if (
        typeof node.name !== 'string' ||
        !/^[a-zA-Z][a-zA-Z0-9_]{0,39}$/.test(node.name) ||
        ['constructor', 'prototype', '__proto__'].includes(node.name)
      )
        throw new Error('表单 name 必须是合法字段名')
      if (form.names.has(node.name)) throw new Error(`表单字段名重复：${node.name}`)
      form.names.add(node.name)
      if (!isTextValue(node.label) || !node.label.trim()) throw new Error('输入组件需要非空 label')
      for (const key of ['defaultValue', 'placeholder']) {
        if (node[key] !== undefined && !isTextValue(node[key]))
          throw new Error(`${key} 必须是字符串，且不超过 4000 字符`)
      }
      if (
        node.type === 'Textarea' &&
        node.rows !== undefined &&
        (!Number.isInteger(node.rows) || node.rows < 2 || node.rows > 8)
      )
        throw new Error('Textarea rows 必须是 2–8 的整数')
      if (node.type === 'RadioGroup') {
        if (!Array.isArray(node.options) || !node.options.length || node.options.length > 20)
          throw new Error('RadioGroup options 需要 1–20 个选项')
        const values = new Set()
        for (const option of node.options) {
          if (
            !option ||
            !isTextValue(option.label) ||
            !option.label.trim() ||
            !isTextValue(option.value) ||
            !option.value.trim() ||
            Object.keys(option).some((key) => !['label', 'value'].includes(key)) ||
            values.has(option.value)
          )
            throw new Error('单选选项需要非空 label 和互不重复的 value')
          values.add(option.value)
        }
        if (node.defaultValue !== undefined && !values.has(node.defaultValue))
          throw new Error('单选 defaultValue 必须对应一个选项')
      }
      if (node.type === 'DatePicker') {
        for (const key of ['defaultValue', 'min', 'max']) {
          if (node[key] !== undefined && !isDate(node[key]))
            throw new Error(`DatePicker ${key} 必须是有效的 YYYY-MM-DD 日期`)
        }
        if (
          (node.min && node.max && node.min > node.max) ||
          (node.defaultValue &&
            ((node.min && node.defaultValue < node.min) ||
              (node.max && node.defaultValue > node.max)))
        )
          throw new Error('DatePicker 日期范围不正确')
      }
    }
    if (node.type === 'Button') {
      if (!isTextValue(node.label) || !node.label.trim()) throw new Error('Button 需要非空 label')
      if (node.submit === true) {
        if (!form || node.onClickAction !== undefined)
          throw new Error('提交按钮必须在 Form 内并使用 Form 动作')
        form.hasSubmit = true
      } else if (form || !isSendAction(node.onClickAction)) {
        throw new Error('独立 Button 只支持 send_message 动作；表单内请使用 submit:true')
      }
      for (const key of ['iconStart', 'iconEnd']) {
        if (node[key] !== undefined && !WIDGET_ICON_NAMES.includes(node[key]))
          throw new Error('按钮图标名称不受支持')
      }
    }
    const enums = {
      align: ['start', 'center', 'end', 'baseline', 'stretch'],
      justify: ['start', 'center', 'end', 'between', 'around', 'evenly'],
      weight: ['normal', 'medium', 'semibold', 'bold'],
      textAlign: ['start', 'center', 'end'],
      variant:
        node.type === 'Button'
          ? ['solid', 'soft', 'outline', 'ghost']
          : ['solid', 'soft', 'outline'],
      style: ['primary', 'secondary'],
      size: {
        Card: ['sm', 'md', 'lg', 'full'],
        Title: ['xs', 'sm', 'md', 'lg', 'xl', '2xl'],
        Icon: ['xs', 'sm', 'md', 'lg', 'xl'],
        Text: ['xs', 'sm', 'md', 'lg', 'xl']
      }[node.type] || ['sm', 'md', 'lg']
    }
    for (const [key, values] of Object.entries(enums)) {
      if (node[key] !== undefined && !values.includes(node[key]))
        throw new Error(`${key} 属性不受支持`)
    }
    if (
      node.flex !== undefined &&
      node.flex !== 'auto' &&
      (!Number.isSafeInteger(node.flex) || node.flex < 0)
    )
      throw new Error('flex 必须是非负整数或 auto')
    for (const key of ['gap', 'padding']) {
      if (
        node[key] !== undefined &&
        (!Number.isInteger(node[key]) || node[key] < 0 || node[key] > 8)
      )
        throw new Error(`${key} 必须是 0–8 的整数，单位为 4px`)
    }
    if (node.pill !== undefined && typeof node.pill !== 'boolean')
      throw new Error('pill 必须是布尔值')
    for (const key of ['required', 'disabled', 'submit', 'block']) {
      if (node[key] !== undefined && typeof node[key] !== 'boolean')
        throw new Error(`${key} 必须是布尔值`)
    }
    if (node.color !== undefined) {
      const colors =
        node.type === 'Badge'
          ? ['secondary', 'success', 'warning', 'danger', 'info']
          : ['primary', 'secondary', 'tertiary', 'success', 'warning', 'danger', 'info']
      if (!colors.includes(node.color)) throw new Error('组件颜色不受支持')
    }
    if (node.type === 'Icon' && !WIDGET_ICON_NAMES.includes(node.name))
      throw new Error('图标名称不受支持')
    if (node.type === 'Chart') {
      if (!isTextValue(node.label) || !node.label.trim()) throw new Error('Chart 需要非空 label')
      const isColumnKey = (value) =>
        typeof value === 'string' &&
        /^[a-zA-Z][a-zA-Z0-9_]{0,39}$/.test(value) &&
        !['constructor', 'prototype', '__proto__'].includes(value)
      if (!isColumnKey(node.xAxis)) throw new Error('Chart xAxis 必须是合法数据列名')
      if (!Array.isArray(node.series) || !node.series.length || node.series.length > 3)
        throw new Error('Chart series 必须是包含 1–3 项的数组')
      const seriesKeys = new Set()
      for (const [index, series] of node.series.entries()) {
        const location = `Chart series 第 ${index + 1} 项`
        if (!series || typeof series !== 'object' || Array.isArray(series))
          throw new Error(`${location}必须是对象`)
        if (!['bar', 'line'].includes(series.type))
          throw new Error(`${location}字段 "type" 只支持 bar/line`)
        if (!isColumnKey(series.dataKey))
          throw new Error(`${location}字段 "dataKey" 必须是合法数据列名`)
        if (series.dataKey === node.xAxis)
          throw new Error(`${location}字段 "dataKey" 不能与 xAxis 字段 "${node.xAxis}" 相同`)
        if (seriesKeys.has(series.dataKey))
          throw new Error(`${location}字段 "dataKey" 重复："${series.dataKey}"`)
        seriesKeys.add(series.dataKey)
        if (series.label !== undefined && (!isTextValue(series.label) || !series.label.trim()))
          throw new Error(`${location}字段 "label" 必须是非空字符串，且不超过 4000 字符`)
        const extraKey = Object.keys(series).find(
          (key) => !['type', 'dataKey', 'label'].includes(key)
        )
        if (extraKey !== undefined) throw new Error(`${location}包含不支持的字段 "${extraKey}"`)
      }
      if (!Array.isArray(node.data)) throw new Error('Chart data 必须是数组')
      if (node.data.length > 60)
        throw new Error(`Chart data 最多 60 条记录，当前为 ${node.data.length} 条`)
      const columns = [node.xAxis, ...seriesKeys]
      for (const [index, row] of node.data.entries()) {
        const location = `Chart data 第 ${index + 1} 条`
        if (!row || typeof row !== 'object' || Array.isArray(row))
          throw new Error(`${location}必须是对象`)
        if (!Object.hasOwn(row, node.xAxis))
          throw new Error(`${location}缺少横轴字段 "${node.xAxis}"（由 xAxis 指定）`)
        if (!isTextValue(row[node.xAxis]) || !row[node.xAxis].trim())
          throw new Error(`${location}字段 "${node.xAxis}" 必须是非空字符串，且不超过 4000 字符`)
        for (const { dataKey } of node.series) {
          if (!Object.hasOwn(row, dataKey))
            throw new Error(`${location}缺少系列字段 "${dataKey}"（由 series.dataKey 指定）`)
          if (!Number.isFinite(row[dataKey]))
            throw new Error(`${location}字段 "${dataKey}" 必须是有限数字`)
          if (Math.abs(row[dataKey]) > 1e12)
            throw new Error(`${location}字段 "${dataKey}" 的绝对值不能超过 1e12`)
        }
        const extraKey = Object.keys(row).find((key) => !columns.includes(key))
        if (extraKey !== undefined) throw new Error(`${location}包含未声明的字段 "${extraKey}"`)
      }
      if (
        node.height !== undefined &&
        (!Number.isInteger(node.height) || node.height < 180 || node.height > 400)
      )
        throw new Error('Chart height 必须是 180–400 的整数')
      if (node.showLegend !== undefined && typeof node.showLegend !== 'boolean')
        throw new Error('Chart showLegend 必须是布尔值')
    }
    if (node.type === 'Image') {
      if ((node.src !== undefined) === (node.path !== undefined))
        throw new Error('Image 必须指定 src 或 path 之一')
      if (node.path !== undefined) {
        if (
          !isTextValue(node.path) ||
          !node.path.startsWith('/') ||
          node.path.startsWith('//') ||
          /[\\\0]/.test(node.path) ||
          node.path.includes('://') ||
          node.path.split('/').includes('..')
        )
          throw new Error('Image path 必须是无跳转的沙盒虚拟绝对路径')
      } else {
        if (!isTextValue(node.src))
          throw new Error('Image src 必须是 HTTPS URL 或同源绝对路径')
        const link = /^\[[^[\]\r\n]*\]\(([^()\s]+)\)$/.exec(node.src)
        const src = link?.[1] ?? node.src
        if (!src || /[\s\\]/.test(src))
          throw new Error('Image src 必须是 HTTPS URL 或同源绝对路径')
        let url
        try {
          url = new URL(src, 'https://widget.invalid')
        } catch {
          throw new Error('Image src 必须是 HTTPS URL 或同源绝对路径')
        }
        if (
          (!src.startsWith('https://') && (!src.startsWith('/') || src.startsWith('//'))) ||
          url.protocol !== 'https:' ||
          url.username ||
          url.password
        )
          throw new Error('Image src 必须是无凭据的 HTTPS URL 或同源绝对路径')
        node.src = src
      }
      if (!isTextValue(node.alt) || !node.alt.trim()) throw new Error('Image 需要非空 alt')
      if (node.caption !== undefined && (!isTextValue(node.caption) || !node.caption.trim()))
        throw new Error('Image caption 必须是非空短语')
      if (node.fit !== undefined && !['cover', 'contain'].includes(node.fit))
        throw new Error('Image fit 只支持 cover/contain')
      if (
        node.aspectRatio !== undefined &&
        (!Number.isFinite(node.aspectRatio) || node.aspectRatio < 0.5 || node.aspectRatio > 3)
      )
        throw new Error('Image aspectRatio 必须是 0.5–3 的数字')
    }
    if (textTypes.has(node.type) && !isTextValue(node.value))
      throw new Error('文本组件需要 value 字符串')
    if (node.type === 'Badge' && !isTextValue(node.label)) throw new Error('组件需要 label 字符串')
    if (
      node.type === 'ListViewItem' &&
      node.onClickAction !== undefined &&
      !isComposeAction(node.onClickAction)
    )
      throw new Error('列表项只支持 compose 动作和非空 text')
    if (containerTypes.has(node.type)) {
      if (!Array.isArray(node.children)) throw new Error('容器需要 children 数组')
      if (node.type === 'ImageGroup' && !node.children.length)
        throw new Error('ImageGroup 至少需要一张图片')
      for (const child of node.children) {
        if (node.type === 'ImageGroup' && child?.type !== 'Image')
          throw new Error('ImageGroup 只能包含 Image')
        if (node.type === 'ListView' && child?.type !== 'ListViewItem')
          throw new Error('ListView 只能包含 ListViewItem')
        visit(
          child,
          depth + 1,
          inActionItem || (node.type === 'ListViewItem' && !!node.onClickAction),
          form
        )
      }
      if (node.type === 'Form' && (!form.names.size || !form.hasSubmit))
        throw new Error('Form 需要输入字段和 submit:true 的 Button')
    }
  }
  visit(root, 1)
  return root
}

/** 按可见标签整理用户填写结果，作为普通用户消息发送。 */
export function widgetFormMessage(form, entries) {
  const values = new Map(entries)
  const lines = [form.onSubmitAction.payload.text]
  function collect(node) {
    if (fieldTypes.has(node.type) && !node.disabled) {
      const value = values.get(node.name) || ''
      if (typeof value !== 'string' || value.length > 4000)
        throw new Error('表单字段最多填写 4000 字符')
      if (node.required && !value.trim()) throw new Error(`请填写${node.label}`)
      const label =
        node.type === 'RadioGroup'
          ? node.options.find((option) => option.value === value)?.label
          : value
      if (value && label === undefined) throw new Error(`请选择有效的${node.label}`)
      lines.push(`${node.label}：${label || '未填写'}`)
    }
    node.children?.forEach(collect)
  }
  collect(form)
  return lines.join('\n')
}

/** 在 Markdown token 边界只提升显式启用且闭合的 widget 围栏。 */
export function widgetFencePlugin(md) {
  const original = md.renderer.rules.fence
  md.renderer.rules.fence = (tokens, index, options, env, renderer) => {
    const token = tokens[index]
    if (!env.widgets || token.info.trim() !== 'widget')
      return original(tokens, index, options, env, renderer)
    const lastLine = env.widgetLines?.[token.map[1] - 1] || ''
    const closing = new RegExp(`^ {0,3}${token.markup[0]}{${token.markup.length},}\\s*$`)
    const closed = closing.test(lastLine)
    const lines = env.sourceLines
      ? ` data-source-start="${token.map[0] + 1}" data-source-end="${token.map[1]}" data-reference-excluded`
      : ''
    return `<div class="widget-preview-render"${lines}><pre class="widget-source" hidden>${md.utils.escapeHtml(token.content)}</pre><div class="widget-mount" data-widget-closed="${closed}"></div></div>`
  }
}
