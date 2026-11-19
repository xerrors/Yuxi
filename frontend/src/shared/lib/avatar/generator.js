import { Avatar, Style } from '@dicebear/core'
import glyphs from '@dicebear/styles/glyphs.json' with { type: 'json' }
import clay from '@dicebear/styles/clay.json' with { type: 'json' }
import shapeGrid from '@dicebear/styles/shape-grid.json' with { type: 'json' }
import gaze from '@dicebear/styles/gaze.json' with { type: 'json' }
import lorelei from '@dicebear/styles/lorelei.json' with { type: 'json' }
import notionists from '@dicebear/styles/notionists.json' with { type: 'json' }
import { presets, paletteOptions } from './presets.js'

export const defaultStyles = { user: 'notionists', agent: 'lorelei', session: 'gaze' }

const definitions = new Map(
  Object.entries({ glyphs, clay, 'shape-grid': shapeGrid, gaze, lorelei, notionists })
)
const styles = new Map()

/** DiceBear 会校验并深拷贝定义；首次使用某个风格时才构造并复用它。 */
function getStyle(name, dark) {
  const nightGlyphs = name === 'glyphs' && dark
  const key = nightGlyphs ? 'glyphs-night' : name
  if (!styles.has(key)) {
    let definition = definitions.get(name)
    if (nightGlyphs) {
      // 只开放 Glyphs 的底纸颜色；蒙版与所有几何保持上游定义。
      definition = structuredClone(definition)
      definition.colors.paper = { values: ['#ffffff'] }
      definition.canvas.elements[1].children[0].attributes.fill = { type: 'color', name: 'paper' }
    }
    styles.set(key, new Style(definition))
  }
  return styles.get(key)
}

/** 解析独立的明暗策略，纯生成器由调用方传入系统偏好。 */
export function resolveDark(dark = 'auto', systemDark = false) {
  if (!['on', 'off', 'auto'].includes(dark)) throw new RangeError(`不支持的 dark：${dark}`)
  return dark === 'on' || (dark === 'auto' && systemDark)
}

/** 将项目 preset 名解析为普通 DiceBear options，未知配置显式报错。 */
export function resolvePreset(style, preset = 'default', dark = false) {
  if (!definitions.has(style)) throw new RangeError(`不支持的头像 style：${style}`)
  if (!Object.hasOwn(presets, preset)) throw new RangeError(`不支持的头像 preset：${preset}`)
  return structuredClone(paletteOptions(style, preset, dark))
}

/** 按固定版本、风格、预设与身份生成 SVG；圆形裁切由展示层统一指定。 */
export function generate(
  { style, preset = 'default', seed = 'default', shape = 'circle', dark = 'auto' },
  systemDark = false
) {
  const isDark = resolveDark(dark, systemDark)
  const options = resolvePreset(style, preset, isDark)
  if (!['circle', 'square', 'rounded'].includes(shape))
    throw new RangeError(`不支持的头像 shape：${shape}`)
  if (typeof seed !== 'string' || !seed.trim()) throw new TypeError('头像 seed 必须是非空字符串')
  return new Avatar(getStyle(style, isDark), {
    ...options,
    seed,
    size: 256,
    borderRadius: { circle: 50, rounded: 18, square: 0 }[shape],
    idRandomization: false
  }).toString()
}

/** 将 SVG 隔离在 img 中，避免多个头像之间的 SVG ID 冲突。 */
export function imageUrl(svg) {
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
}
