import {
  BookOpenText,
  CalendarCheck,
  ChartNoAxesCombined,
  EthernetPort,
  FileText,
  FlaskConical,
  Globe,
  Image,
  LayoutDashboard,
  Microscope,
  PanelsTopLeft,
  Paintbrush,
  Search,
  Sheet,
  Video,
  WandSparkles,
  Workflow
} from '@lucide/vue'

const EXTENSION_ICON_RULES = [
  { keywords: ['tencent-meeting', '腾讯会议'], icon: Video },
  { keywords: ['weknora'], icon: Search },
  { keywords: ['attendance', '考勤'], icon: CalendarCheck },
  { keywords: ['mysql-reporter'], icon: ChartNoAxesCombined },
  { keywords: ['chat-widgets'], icon: LayoutDashboard },
  { keywords: ['browser', 'playwright'], icon: PanelsTopLeft },
  { keywords: ['skill-builder'], icon: Workflow },
  { keywords: ['design'], icon: Paintbrush },
  { keywords: ['document', 'pdf', 'docx', 'docs', 'wps'], icon: FileText },
  { keywords: ['knowledge', 'deepwiki', '知识库'], icon: BookOpenText },
  { keywords: ['research'], icon: Microscope },
  { keywords: ['xlsx', 'excel', 'sheet'], icon: Sheet },
  { keywords: ['image'], icon: Image },
  { keywords: ['api'], icon: EthernetPort },
  { keywords: ['web', 'html'], icon: Globe },
  { keywords: ['test'], icon: FlaskConical }
]

/** Skill 和 MCP 共用名称或 slug 的关键词图标映射。 */
export const getSkillIcon = (slug, defaultIcon = WandSparkles) => {
  const normalizedSlug = String(slug || '').toLowerCase()
  return (
    EXTENSION_ICON_RULES.find((rule) =>
      rule.keywords.some((keyword) => normalizedSlug.includes(keyword))
    )?.icon || defaultIcon
  )
}
