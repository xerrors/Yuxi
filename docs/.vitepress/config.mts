import { defineConfig } from 'vitepress'
import markdownItTaskCheckbox from 'markdown-it-task-checkbox'


// https://vitepress.dev/reference/site-config
export default defineConfig({
  lang: 'zh-CN',
  title: "Yuxi",
  description: "Yuxi 是一个可私有部署的多租户知识智能体平台，统一 RAG、知识图谱、LangGraph 多智能体、MCP/Skills、沙盒与权限管理。",
  base: '/Yuxi/',
  srcExclude: ['vibe/**'],
  sitemap: {
    hostname: 'https://xerrors.github.io/Yuxi/'
  },
  head: [
    ['link', { rel: 'icon', href: '/Yuxi/favicon.svg' }],
    ['link', { rel: 'alternate icon', href: '/Yuxi/favicon.ico' }],
    ['meta', { name: 'theme-color', content: '#F3BA32' }],
    ['meta', { name: 'keywords', content: 'Yuxi, AI Agent, RAG, knowledge graph, LangGraph, MCP, self-hosted, multi-agent, knowledge base' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'Yuxi' }],
    ['meta', { property: 'og:title', content: 'Yuxi · 让知识真正参与每一次行动' }],
    ['meta', { property: 'og:description', content: '可私有部署的多租户知识智能体平台，统一知识、工具、多智能体执行与团队治理。' }],
    ['meta', { property: 'og:image', content: 'https://xerrors.oss-cn-shanghai.aliyuncs.com/github/yuxi/docs/home/yuxi-web-og-1200x630-v1.0.0.png' }],
    ['meta', { property: 'og:image:width', content: '1200' }],
    ['meta', { property: 'og:image:height', content: '630' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:title', content: 'Yuxi · Self-hosted knowledge agent platform' }],
    ['meta', { name: 'twitter:description', content: 'RAG, knowledge graphs, multi-agent execution, MCP/Skills, sandboxed artifacts, and access control in one workspace.' }],
    ['meta', { name: 'twitter:image', content: 'https://xerrors.oss-cn-shanghai.aliyuncs.com/github/yuxi/docs/home/yuxi-web-og-1200x630-v1.0.0.png' }]
  ],
  ignoreDeadLinks: [
    /localhost/
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
      { text: '项目介绍', link: '/intro/project-overview' },
      { text: '快速开始', link: '/intro/quick-start' },
      { text: '智能体开发', link: '/agents/agents-config' },
      { text: '机制详解', link: '/mechanisms/' },
      { text: 'English', link: 'https://github.com/xerrors/Yuxi/blob/main/README.en.md' }
    ],

    sidebar: [
      {
        text: '简介',
        items: [
          { text: '认识 Yuxi', link: '/intro/project-overview' },
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
          { text: '开发智能体后端', link: '/agents/agent-backend-development' },
          { text: 'Agent 请求队列', link: '/agents/agent-request-queue' },
          { text: '工具系统', link: '/agents/tools-system' },
          { text: '中间件', link: '/agents/middleware' },
          { text: '智能体评估', link: '/agents/agent-evaluation' },
          { text: '沙盒配置与运维', link: '/agents/sandbox-architecture' },
          { text: 'MCP 集成', link: '/agents/mcp-integration' },
          { text: 'Skills 管理', link: '/agents/skills-management' },
          { text: '子智能体', link: '/agents/subagents-management' }
        ]
      },
      {
        text: '机制详解',
        items: [
          { text: '阅读路径', link: '/mechanisms/' },
          { text: 'Agent 运行时上下文', link: '/mechanisms/agent-runtime' },
          { text: '沙盒与文件系统', link: '/mechanisms/sandbox' },
          { text: '上下文压缩', link: '/mechanisms/context-compression' },
          { text: '知识库', link: '/mechanisms/knowledge-base' }
        ]
      },
      {
        text: '高级配置',
        items: [
          { text: '配置系统详解', link: '/advanced/configuration' },
          { text: 'Agent 并发容量', link: '/advanced/agent-concurrency-capacity' },
          { text: 'Langfuse 集成', link: '/advanced/langfuse-integration' },
          { text: '文档解析', link: '/advanced/document-processing' },
          { text: '文档处理限制', link: '/advanced/document-limits' },
          { text: '知识库管理与 API', link: '/advanced/knowledge-base-operations' },
          { text: '文档导入与查询 API', link: '/advanced/knowledge-base-api' },
          { text: '知识导图与知识图谱', link: '/advanced/knowledge-base-graph' },
          { text: '品牌自定义', link: '/advanced/branding' },
          { text: '其他配置', link: '/advanced/misc' },
          { text: '网页搜索', link: '/advanced/web-search' },
          { text: '服务端口', link: '/advanced/ports' },
          { text: '生产部署', link: '/advanced/deployment' },
          { text: 'API Key 外部集成', link: '/advanced/api-key-integration' },
          { text: '第三方认证', link: '/advanced/third-party-auth' }
        ]
      },
      {
        text: '开发指南',
        items: [
          { text: '参与贡献', link: '/develop-guides/contributing' },
          { text: '并行工作树与隔离环境', link: '/develop-guides/parallel-worktree-environments' },
          { text: '文档编写与维护', link: '/develop-guides/documentation-guidelines' },
          { text: '开发路线图', link: '/develop-guides/roadmap' },
          { text: '版本变更记录', link: '/develop-guides/changelog' },
          { text: '界面设计规范', link: '/develop-guides/design' },
          { text: '测试规范', link: '/develop-guides/testing-guidelines' },
          { text: '并发优化与评测', link: '/develop-guides/decisions/implemented/2026-09-07-agent-concurrency-optimization' },
          { text: 'Yuxi Spec Loop', link: '/develop-guides/spec-loop' },
          { text: '工程信任系统', link: '/develop-guides/engineering-trust' },
          { text: '工程决策记录', link: '/develop-guides/decisions/README' },
          { text: '工程事故复盘', link: '/develop-guides/postmortems/README' },
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
