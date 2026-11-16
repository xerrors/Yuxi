import {
  BookOpenText,
  EthernetPort,
  FileText,
  FlaskConical,
  Globe,
  Image,
  Microscope,
  Paintbrush,
  Sheet,
  WandSparkles
} from '@lucide/vue'

const SKILL_ICON_RULES = [
  { keywords: ['design'], icon: Paintbrush },
  { keywords: ['document', 'pdf', 'docx', 'docs', 'wps'], icon: FileText },
  { keywords: ['knowledge'], icon: BookOpenText },
  { keywords: ['research'], icon: Microscope },
  { keywords: ['xlsx', 'excel', 'sheet'], icon: Sheet },
  { keywords: ['image'], icon: Image },
  { keywords: ['api'], icon: EthernetPort },
  { keywords: ['web', 'html'], icon: Globe },
  { keywords: ['test'], icon: FlaskConical }
]

/** 根据 slug 关键词返回 Skill 的 Lucide 图标。 */
export const getSkillIcon = (slug) => {
  const normalizedSlug = String(slug || '').toLowerCase()
  return (
    SKILL_ICON_RULES.find((rule) =>
      rule.keywords.some((keyword) => normalizedSlug.includes(keyword))
    )?.icon || WandSparkles
  )
}
