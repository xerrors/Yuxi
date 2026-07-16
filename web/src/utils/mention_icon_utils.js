import { BookOpen, Bot, Plug, WandSparkles } from 'lucide-vue-next'

export const MENTION_ICON_SIZE = 15
export const MENTION_ICON_STROKE_WIDTH = 2.2

// 注意：file 类型的图标由 FileTypeIcon 组件直接渲染，此处仅处理其余 mention 类型。
const MENTION_TYPE_ICON_COMPONENTS = {
  knowledge: BookOpen,
  skill: WandSparkles,
  mcp: Plug,
  subagent: Bot
}

export const getMentionIconComponent = (type) => MENTION_TYPE_ICON_COMPONENTS[type] || Plug

export const getMentionIconStyle = () => null
