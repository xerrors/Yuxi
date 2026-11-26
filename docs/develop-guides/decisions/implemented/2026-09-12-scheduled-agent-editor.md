# 定时任务编辑器的加载状态与行布局

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/agents/ui/ScheduledAgentEditor.vue

## 问题

项目选择器的预加载会重复请求全局项目列表，共用 loading 的侧边栏随之隐藏已有项目和对话。编辑器依赖旧下拉组件内部类名，以及直接在 fieldset 上布局，造成控件对齐偏差。历史记录缺少水平内边距，悬浮背景紧贴文本。

## 决策

项目 store 用 hasLoaded 区分首次读取与后续刷新，包括成功返回空列表的情况。AppLayout 只在首次读取时向侧边栏传递阻断内容的加载和错误状态；项目选择器继续展示刷新错误与重试入口。选择器预加载复用已完成或正在进行的读取，用户打开下拉列表时仍请求最新数据。用户会话建立和退出时清空项目缓存、重置首次加载状态并使旧请求失效。

ScheduledAgentEditor 的执行智能体选择复用 ActionDropdown 与 ActionTrigger，提供搜索、当前选中状态和空结果提示，只修改当前任务的 agent_slug。在控件容器对齐当前共享下拉组件，重复频率通过 fieldset 内部网格排列。ScheduledAgentsView 的历史记录始终保留水平和垂直内边距，悬浮只改变背景。新建名称默认“新建定时任务”；打开编辑器和切换任务时聚焦并选中名称，草稿自动创建成功时保留当前输入焦点。

## 替代方案

为弹窗复制一份项目列表会增加状态同步负担；完全取消项目刷新会让其他页面的修改不可见。保留共享数据、区分首次加载和后台刷新可以同时满足及时性与侧边栏稳定性。历史记录保留固定内边距，使静止和悬浮状态具有相同文本位置。

## 后果

后台刷新失败时侧边栏继续展示已有数据，项目选择器负责错误反馈。后端调度、任务执行与自动保存协议保持现有契约。修复范围明确且没有待裁决的接口或数据风险，直接记录为 implemented。

## 验证

[项目 store 测试](https://github.com/xerrors/Yuxi/blob/main/frontend/test/unit/projectsStore.test.js)覆盖空列表加载完成、刷新期间与失败后的列表保留，以及退出后迟到响应不能跨会话写回。[浏览器脚本](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/scheduledAgentEditor.js)在实际页面使用固定列表响应，检查打开详情不重复请求项目、刷新期间侧边栏 DOM 保留、名称焦点与默认值、历史记录内边距、频率垂直对齐及窄屏溢出，并生成浅色、深色和窄屏截图。该浏览器验证覆盖真实组件装配，不代表真实任务执行或持久化验证。
