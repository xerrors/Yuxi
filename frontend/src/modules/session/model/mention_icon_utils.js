import { BookOpen, Bot, MessageCircle, Plug, Puzzle, WandSparkles } from '@lucide/vue'
import { getSkillIcon } from '../../../shared/lib/skill_icon_utils.js'

export const MENTION_ICON_SIZE = 15
export const MENTION_ICON_STROKE_WIDTH = 2.2

// 注意：file 类型的图标由 FileTypeIcon 组件直接渲染，此处仅处理其余 mention 类型。
const MENTION_TYPE_ICON_COMPONENTS = {
  agent: Bot,
  session: MessageCircle,
  knowledge: BookOpen,
  skill: WandSparkles,
  mcp: Plug
}

/** 目录候选与正文引用共用资源用途图标。 */
export const getMentionIconComponent = (type, value, label = '') => {
  if (type === 'mcp') return getSkillIcon(`${value} ${label}`, Plug)
  if (type === 'plugin') return getSkillIcon(`${value} ${label}`, Puzzle)
  if (type === 'skill') return getSkillIcon(value)
  return MENTION_TYPE_ICON_COMPONENTS[type] || Plug
}
