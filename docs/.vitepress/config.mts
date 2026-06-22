import { defineConfig } from 'vitepress'
import markdownItTaskCheckbox from 'markdown-it-task-checkbox'


// https://vitepress.dev/reference/site-config
export default defineConfig({
  lang: 'zh-CN',
  title: "Yuxi",
  description: "语析",
  base: '/Yuxi/',
  ignoreDeadLinks: [
    /localhost/,
    /CONTRIBUTING$/,
    /docker-compose\.yml$/,
    /^\.\/intro\//
  ],
  markdown: {
    config: (md) => {
      md.use(markdownItTaskCheckbox)
    }
  },
  themeConfig: {
    // https://vitepress.dev/reference/default-theme-config
    logo: "/favicon.svg",
    nav: [
      { text: '快速开始', link: '/intro/quick-start' },
      { text: '智能体开发', link: '/agents/agents-config' }
    ],

    sidebar: [
      {
        text: '简介',
        items: [
          { text: '什么是 Yuxi？', link: '/intro/project-overview' },
          { text: '快速开始', link: '/intro/quick-start' },
          { text: '命令行工具', link: '/intro/cli' },
          { text: '模型配置', link: '/intro/model-config' },
          { text: '知识库与知识图谱', link: '/intro/knowledge-base' },
          { text: '知识库评估', link: '/intro/evaluation' }
        ]
      },
      {
        text: '智能体开发',
        items: [
          { text: '智能体配置', link: '/agents/agents-config' },
          { text: '工具系统', link: '/agents/tools-system' },
          { text: '中间件', link: '/agents/middleware' },
          { text: '智能体评估', link: '/agents/agent-evaluation' },
          { text: '沙盒架构与设计', link: '/agents/sandbox-architecture' },
          { text: 'MCP 集成', link: '/agents/mcp-integration' },
          { text: 'Skills 管理', link: '/agents/skills-management' },
          { text: '子智能体', link: '/agents/subagents-management' }
        ]
      },
      {
        text: '高级配置',
        items: [
          { text: '配置系统详解', link: '/advanced/configuration' },
          { text: 'Langfuse 集成', link: '/advanced/langfuse-integration' },
          { text: '文档解析', link: '/advanced/document-processing' },
          { text: '品牌自定义', link: '/advanced/branding' },
          { text: '其他配置', link: '/advanced/misc' },
          { text: '生产部署', link: '/advanced/deployment' },
          { text: 'API Key 外部集成', link: '/advanced/api-key-integration' }
        ]
      },
      {
        text: '开发指南',
        items: [
          { text: '参与贡献', link: '/develop-guides/contributing' },
          { text: '开发路线图', link: '/develop-guides/roadmap' },
          { text: '版本变更记录', link: '/develop-guides/changelog' },
          { text: '界面设计规范', link: '/develop-guides/design' },
          { text: '测试规范', link: '/develop-guides/testing-guidelines' },
        ]
      }
    ],

    socialLinks: [
      { icon: 'github', link: 'https://github.com/xerrors/Yuxi' }
    ],

    footer: {
      message: '本项目基于 MIT License 开源，欢迎使用和贡献。',
      copyright: 'Copyright © 2025-present Yuxi'
    },

    editLink: {
      pattern: 'https://github.com/xerrors/Yuxi/edit/main/docs/:path',
      text: '在 GitHub 上编辑此页'
    },

    lastUpdated: {
      text: '最后更新时间',
      formatOptions: {
        dateStyle: 'full',
        timeStyle: 'medium'
      }
    },

    search: {
      provider: 'local'
    },

    docFooter: {
      prev: '上一页',
      next: '下一页'
    }
  },
})
