import { BookOpen, Bot, MessageCircle, Plug, WandSparkles } from '@lucide/vue'
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

export const getMentionIconComponent = (type, value, label = '') =>
  type === 'mcp'
    ? getSkillIcon(`${value} ${label}`, Plug)
    : type === 'skill'
      ? getSkillIcon(value)
      : MENTION_TYPE_ICON_COMPONENTS[type] || Plug
