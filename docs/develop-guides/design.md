# 界面设计规范

## 总览

语析平台的 UI 设计致力于打造简洁、高效、一致的用户体验。设计理念以功能为核心，视觉表现为辅助，避免过度装饰，让用户专注于知识管理与 Agent 开发的核心任务。

## 设计原则

### 简洁

- 界面元素应精简，去除不必要的视觉元素
- 每个组件应有明确的用途，避免功能性冗余
- 留白适度，提升可读性和操作效率

### 一致性

- 全局保持统一的视觉风格和交互模式
- 颜色、字体、间距等遵循 [base.css](web/src/assets/css/base.css) 中的设计变量
- 相似功能的组件使用相同的设计模式

### 稳定性

- 禁止悬停位移：交互元素在悬停时应保持位置稳定，避免视觉跳动
- 避免过度阴影：阴影仅用于层级区分，不得用于装饰目的
- 避免过度渐变：渐变色应谨慎使用，优先使用纯色或微弱渐变

## 禁止项

以下 UI 做法在本项目中明确禁止：

| 禁止项 | 说明 |
|--------|------|
| 悬停位移 | 组件在 hover 状态下不得产生位置偏移 |
| 过度阴影 | 禁止使用浓重阴影进行装饰，仅在必要时使用微弱阴影区分层级 |
| 过度渐变 | 禁止使用夸张渐变，优先使用纯色或微弱渐变 |
| 装饰性动效 | 禁止非功能性的装饰动画 |
| 悬停放大 | 禁止 hover 时放大组件尺寸 |
| 颜色滥用 | 禁止使用过于鲜艳或刺眼的颜色 |

## 组件规范

### 技术栈

- 包管理器：pnpm
- 图标库：lucide-vue-next（注意尺寸控制）
- 样式语言：LESS
- 样式变量：必须使用 [base.css](web/src/assets/css/base.css) 中定义的颜色变量

### 开发规范

1. **API 接口**：所有接口定义在 `web/src/apis` 目录下
2. **样式复用**：优先复用现有组件和样式，避免重复定义
3. **响应式设计**：适配不同屏幕宽度，保持布局稳定
4. **无障碍设计**：保证基本的无障碍支持，如适当的对比度、键盘可操作等

### 颜色变量

必须从 `web/src/assets/css/base.css` 中获取以下变量：

```less
// 典型变量示例（实际以 base.css 为准）
@primary-color: // 主色调
@text-color: // 文本颜色
@background-color: // 背景颜色
@border-color: // 边框颜色
// ... 其他变量
```

### 组件示例

```vue
<template>
  <div class="my-component">
    <LucideIcon :icon="Icons.Settings" :size="16" />
    <span>设置</span>
  </div>
</template>

<style lang="less" scoped>
@import '@/assets/css/base.css';

.my-component {
  display: flex;
  align-items: center;
  gap: 8px;
  color: @text-color;
  // 简洁样式，无阴影、无渐变、无悬停位移
}
</style>
```

## 参考资料

- [base.css](web/src/assets/css/base.css) - 全局样式变量
- [前端开发规范](AGENTS.md#前端开发规范) - AGENTS.md 中的前端开发规范
