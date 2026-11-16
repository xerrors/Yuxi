---
name: chat-widgets
description: "在普通聊天中直接展示图片、图文卡片和数值图表，或用简短表单收集用户回答。展示图片或截图时优先 Image/ImageGroup；图片需要配合说明、参数或比较时组合 Card、Row、Col；分类比较用柱状图，时间趋势用折线图；需要填写多个条件时用 Form、文本输入、单选和日期。按用途主动读取本 Skill，无需用户指定组件或 JSON；普通文字回答保持 Markdown。"
version: "2026.10.25.1"
---

# 对话组件

## 普通聊天的直接响应

按用户目的选结构，先完成数据获取工具，再在最终回复中输出下列对应模板，替换全部示例文字、数据和图片地址：

| 用户目的 | 最终回复 |
|---|---|
| 给出分类数值，希望直观比较、查看主要占比 | 简短结论 + Card 中的柱状图 + 单位与来源 |
| 展示一张或多张图片 | 单张以 Image 为根，多张以 ImageGroup 为根；短语用 Image.caption 放在左下角 |
| 图片需要配合说明或比较 | Card 内用 Row、Col 组合 Image 与文字 |
| 需要用户填写几个条件再继续 | Card 中的 Form，按需放文本输入、单选、日期和提交按钮 |

这些短请求直接在回复里呈现内容即可。组件无需创建文件、执行脚本或更新待办。每个模板的开头是三个反引号紧接 `widget`，结尾是三个反引号。协议使用 Markdown 围栏，`<widget>` 标签不会被渲染。所有要展示的围栏放在最后一次工具调用之后的最终回复中，用户才能在正式消息里看到它。

### 分类比较模板

```widget
{
  "type": "Card",
  "size": "full",
  "children": [
    {
      "type": "Title",
      "value": "阅读时长对比"
    },
    {
      "type": "Caption",
      "value": "示例数据 · 单位：分钟"
    },
    {
      "type": "Chart",
      "label": "四类阅读时长",
      "xAxis": "activity",
      "data": [
        {
          "activity": "论文",
          "minutes": 120
        },
        {
          "activity": "技术文档",
          "minutes": 80
        },
        {
          "activity": "笔记",
          "minutes": 45
        },
        {
          "activity": "网页",
          "minutes": 60
        }
      ],
      "series": [
        {
          "type": "bar",
          "dataKey": "minutes",
          "label": "阅读时长"
        }
      ]
    }
  ]
}
```

分类和数值逐项对应用户数据。模板中的 activity、minutes 都是示例数据列名；数据列改名时，同步修改 xAxis 和 series[].dataKey。时间趋势使用 line，xAxis 引用实际日期列名。字段对应关系按下文“图表与图片组合”校验。

### 图片优先展示

用户主要想看图片时，单张直接使用 Image，多张使用 ImageGroup，让图片紧接简短引导出现。caption 是可选短语，叠放在图片左下角；没有短语时只显示图片。

```widget
{
  "type": "Image",
  "src": "https://example.com/replace-with-real-image.jpg",
  "alt": "用户提供的户外风景图片",
  "caption": "周末去户外"
}
```

纯图片展示优先合并为一个 ImageGroup，children 只放 Image。前端使用统一 5:4 缩略图横向排列，窄屏可横向滑动，点击查看完整图片并切换。需要每张图对应说明、参数或对比时，使用下面的图文组合；Image 和 ImageGroup 都可以放在 Card、Row、Col 内。

```widget
{
  "type": "ImageGroup",
  "children": [
    {
      "type": "Image",
      "path": "/home/user/workspace/projects/<project-dir>/first.png",
      "alt": "第一张生成图片"
    },
    {
      "type": "Image",
      "path": "/home/user/workspace/projects/<project-dir>/second.png",
      "alt": "第二张生成图片",
      "caption": "另一个方案"
    }
  ]
}
```

示例 URL 和文件路径均为占位符，替换为用户提供或工具确认存在的实际来源。尚未实际读取画面时，alt 和短语沿用用户描述。

纯图片展示以对话正文中的图片为结果：用一两句简短引导后，立即输出一个顶格闭合的 Image 或 ImageGroup 围栏。不要长篇解释搜索过程、来源站点选择、地址验证或 HTTP 状态；没有被要求时不重复讲解图片检索方法。不要只返回 Markdown 图片链接、普通图片 Markdown、源代码或“已找到图片”的文字。需要说明来源时，围栏外最多附上简洁来源链接。Card 用于确有标题、文本或操作等内容的信息卡片；图片本身不构成套 Card 的理由。

### 图文组合与信息层次

图片与文字共同解释一个内容时，用 Card 组织它们。Row 横向组织，Col 纵向组织；较长说明放在 flex:1 的 Col 中，窄屏由前端换行。例子中的图片地址替换为实际来源：

```widget
{
  "type": "Card",
  "size": "full",
  "children": [
    {"type": "Title", "value": "两个房间的采光比较", "size": "lg"},
    {"type": "Caption", "value": "根据用户提供的图片比较"},
    {
      "type": "Row",
      "align": "start",
      "gap": 4,
      "children": [
        {
          "type": "Col", "flex": 1,
          "children": [
            {"type": "Image", "src": "https://example.com/room-a.jpg", "alt": "用户提供的房间 A"},
            {"type": "Title", "value": "房间 A", "size": "sm"},
            {"type": "Text", "value": "这里填写实际观察到的采光特点。"}
          ]
        },
        {
          "type": "Col", "flex": 1,
          "children": [
            {"type": "Image", "src": "https://example.com/room-b.jpg", "alt": "用户提供的房间 B"},
            {"type": "Title", "value": "房间 B", "size": "sm"},
            {"type": "Text", "value": "这里填写与 A 有意义的差异。"}
          ]
        }
      ]
    }
  ]
}
```

按内容决定结构，不为普通回答强加卡片：同一主题放在一个容器中；Title 表达内容主题或核心结论，Text 放必要解释，Caption 放单位、范围或来源，Badge 只表示有意义的状态。正文与卡片各自承载不同信息，避免整段重复。简单展示图片保持图片优先；只有文字确实帮助理解时才组合图文。需要较长解释时保留在围栏外，卡片保留便于浏览的要点。

### 收集信息的表单

用户要求填写，或下一步确实需要几个尚未提供的条件时，提供简短 Form；一个简单问题直接在正文询问即可。只询问当前任务需要的内容，已知条件不重复问。自由回答用 Input，较长说明用 Textarea，少量互斥选项用 RadioGroup，日期用 DatePicker。每个字段有稳定可见的 label；只有必要条件才标 required，不替用户预选尚未确认的偏好。

表单 Card 使用 size:full，标题使用 size:md。按阅读顺序连续放置字段，前端在正文宽度足够时将短字段排为两列，多行备注占整行，提交按钮放在末尾。只有确实需要额外分组时才使用 Row、Col。正文用一句引导说明用途，卡片说明只放填写所需的条件；字段名称、必填标记和提交按钮已经表达的信息无需再逐项复述。

```widget
{
  "type": "Card",
  "size": "full",
  "children": [
    {"type": "Title", "value": "补充出行安排", "size": "md"},
    {"type": "Text", "value": "填写出行条件，提交后生成一天行程。", "size": "sm"},
    {
      "type": "Form",
      "onSubmitAction": {"type": "send_message", "payload": {"text": "请根据以下出行条件安排一天行程。"}},
      "children": [
        {"type": "Input", "name": "destination", "label": "目的地", "required": true, "placeholder": "城市或地区"},
        {"type": "DatePicker", "name": "date", "label": "出行日期", "required": true},
        {
          "type": "RadioGroup", "name": "pace", "label": "游玩节奏", "required": true,
          "options": [{"label": "轻松", "value": "relaxed"}, {"label": "紧凑", "value": "busy"}]
        },
        {"type": "Textarea", "name": "notes", "label": "其他需求", "rows": 2, "placeholder": "选填，例如饮食偏好或同行情况"},
        {"type": "Button", "label": "提交并安排行程", "submit": true, "style": "primary"}
      ]
    }
  ]
}
```

提交先验证必填项和日期范围，再把提交目的与各字段的可见标签、填写值组成一条用户消息发送，模型收到后继续工作。payload.text 写明当前任务以及应如何使用这些回答；按钮文字说明提交后的下一步。点击本身只发送回答，后续工具和业务动作沿正常会话处理。生成期间、提交中或会话不能接收消息时控件只读；失败时保留填写内容并显示原因。编辑器里未发送的草稿、图片和附件保持独立。

独立 Button 也可发送明确的用户请求，例如 `{"type":"Button","label":"按这些条件生成计划","onClickAction":{"type":"send_message","payload":{"text":"请按上面的条件生成计划。"}}}`。当前只支持这一种发送动作，表单内使用 submit:true，由 Form.onSubmitAction 统一提交。

## 共同协议

输出前先完成结构检查：根对象、children 数组及每个节点都完整；所有键和字符串使用成对双引号；字段来自下表，括号与围栏闭合。JSON 按模板跨行缩进，每个节点独立成对象。最终回复只输出检查完成的组件；需要修改的草案在输出前修正，用户看到的是一份完整定稿。

回答以普通 Markdown 说明结论和上下文。在卡片、图表、图片能清晰呈现内容，或表单能方便用户填写必要条件时，补充一个顶格、完整闭合的 `widget` 围栏，内容是严格 JSON。每个围栏的根为 `Card`、`ListView`、`Image`、`ImageGroup` 或 `Form`；围栏外继续放背景、完整明细和来源。图片展示请求遵循上面的简短图片回复规则，避免背景说明挤走对话中的图片。用用户语言表达标签与动作，只展示已有数据或明确标注的示例数据。

先完成数据获取、计划更新等工具调用，再在最终给用户的回复中输出完整组件。组件若只出现在工具调用前的过程消息中，用户可能只看到过程摘要；最终回复必须包含需要展示的 `widget` 围栏。不要仅宣称“图表已绘制”或“图片已展示”。用户请求多个图表时，分别输出多个完整围栏，每个图表保留自己的标题、单位与数据来源。纯图片展示优先合并为一个 ImageGroup；需要说明时可以组合图文。

定稿时按属性表核对定义：字段名和字符串使用成对双引号，只保留当前组件支持的属性，省略未使用的可选字段。Divider、Spacer 的完整定义只有 `{"type":"Divider"}`、`{"type":"Spacer"}`。

这是 Yuxi 借鉴 ChatKit 命名的组件子集。前端使用固定 Vue 组件与双主题样式；字段、大小写和属性必须符合下表。只使用表内属性，不输出 HTML、CSS、脚本、事件代码或任意服务端动作。

| 组件 | 属性与用途 |
|---|---|
| Card | `children`；可选 `size`：sm、md（默认）、lg、full；`padding`。独立 md 最大 440px，嵌套 md 随父容器铺满；表单布局属于最近的 Card，可通过 Row/Col 包含 Form；该 Card 的 md 使用所在容器宽度 |
| Form | `children`、`onSubmitAction`；可选 `gap`、`padding`、`align`、`justify`、`flex`；不能嵌套 Form |
| Input、Textarea | `name`、`label`；可选 `placeholder`、`defaultValue`、`required`、`disabled`；Textarea 可选 `rows`：2–8 |
| RadioGroup | `name`、`label`、`options`（1–20 个 `{label,value}`，value 互不重复）；可选 `defaultValue`、`required`、`disabled` |
| DatePicker | `name`、`label`；可选 `defaultValue`、`min`、`max`（均为 YYYY-MM-DD）、`required`、`disabled` |
| Button | `label`；表单内 `submit:true`，独立使用 `onClickAction`；可选 `style`：primary、secondary，`variant`：solid、soft、outline、ghost，`size`：sm、md、lg，`pill`、`block`、`disabled`、`iconStart`、`iconEnd`（名称同 Icon） |
| Row、Col | `children`；可选 `gap`、`padding`、`align`、`justify`、`flex`；Row 在窄屏换行 |
| ListView | `children`，只放 ListViewItem；嵌套时随父容器铺满并省去重复外框 |
| ListViewItem | `children`；可选 `gap`、`align`、`onClickAction`；有动作时整行可点击，内部只能放非交互组件 |
| Title、Text、Caption | `value`；可选 `size`、`weight`、`textAlign`、`color` |
| Badge | `label`；可选 `color`：secondary、success、warning、danger、info；`variant`：solid、soft（默认）、outline；`size`：sm、md、lg；`pill` |
| Chart | `label`（图表说明）、`data`、`series`、`xAxis`；可选 `showLegend`、`height`；柱状图/折线图 |
| Image | `src`（HTTPS/同源 URL）或 `path`（沙盒虚拟绝对路径），二选一；`alt`（非空图片说明）；可选 `caption`（左下角短语）、`fit`：cover、contain；`aspectRatio`：0.5–3 的数字 |
| ImageGroup | `children`，至少一个 Image；统一 5:4 缩略图横向滑动，不显示滚动条或数量角标；支持作为根节点 |
| Icon | `name`：book-open、document、lightbulb、info、check、plus、search、chevron-right；可选 `size`：xs、sm、md、lg、xl；`color` |
| Divider、Spacer | 只有 `type`；分割线、弹性空白 |

`gap`、`padding` 为 0–8 整数，每单位 4px。`align` 为 start、center、end、baseline、stretch；`justify` 为 start、center、end、between、around、evenly；`flex` 为 0、1、"auto"：0 按内容占宽且不扩展，1 分配剩余空间并保留列的换行基准，"auto" 按内容参与伸缩。不要使用像素字符串、任意颜色或 CSS 对象。

需要让标题占满一行剩余空间时，把 Title 放在 `{"type":"Col","flex":1,"children":[...]}` 中；文字组件保留文本属性，布局属性由外层 Row/Col 承载。

文本 `size`：Title 支持 xs、sm、md、lg、xl、2xl；Text 支持 xs、sm、md、lg、xl；Caption 支持 sm、md、lg。`weight` 为 normal、medium、semibold、bold；`textAlign` 为 start、center、end。文本和 Icon 的 `color` 为 primary、secondary、tertiary、success、warning、danger、info；省略时沿用默认文本层次。pill 为布尔值。

重点信息用 Title（lg）+ Text + Caption；并列数值用 Row 和 Col（gap:1、flex:1）。双主题由前端调整表面、控件、边框与对比度，模型只选择语义颜色。

## 图表与图片组合

组件用于呈现内容。比较分类用柱状图，观察时间趋势用折线图；Card 内放 Title、Caption、Chart。纯图片展示优先使用 Image 或 ImageGroup；图文说明可组合 Card、Row、Col。短语可用 caption 放在图片左下角，较长说明和来源放围栏外。

Chart 的字段引用规则适用于所有业务数据。xAxis 是 data 中字符串列的**字段名引用**，不是坐标轴类型；series[].dataKey 是 data 中数值列的字段名引用。先确定实际数据列名，再从这些列名填写 xAxis 和 dataKey；列名改变时同步修改引用，保留列值中的用户数据。

输出前逐条核对 data：

- 每条记录都包含 xAxis 指定的字段，值为非空字符串，最多 4000 字符。
- 每条记录都包含所有 series[].dataKey 指定的字段，值为有限数字，绝对值不超过 1e12；0 和负数有效。
- data 只包含上述字段。series 为 1–3 个 `{type:"bar"或"line",dataKey:"列名",label:"图例文字"}`，dataKey 互不重复，且不同于 xAxis。

Chart.data 为 0–60 条记录，按数组元素数量计数，与 JSON 文本行数无关。列名使用字母开头的字母/数字/下划线，最多 40 字符，禁用 constructor、prototype、__proto__。height 是 180–400 的整数像素值，默认 240；showLegend 默认 true。不要输出 ECharts option、formatter、函数或任意系列属性；单位放 Caption。未给出数据时不要编造结果，使用空 data 或明确标记示例数据。

天气预报、价格等时效请求使用实际查询结果绘图。优先使用已提供的联网查询或对应领域工具，例如可用的 `web_search`。搜索配额耗尽或查询失败时更换公开来源；若现有沙盒允许只读网络查询，可以直接读取公开逐日预报 API，按原工具审批处理。[Open-Meteo](https://open-meteo.com/en/docs) 的 `https://api.open-meteo.com/v1/forecast` 提供 `daily=temperature_2m_max,temperature_2m_min`，设置当地时区和 `forecast_days=7`，按返回的 daily.time 及温度列绘图；坐标对应实际城市。来源不覆盖所需日期时继续查询其他来源。

写明来源、查询时间、日期范围和单位。比如两个城市未来七天的天气，分别用两个 Chart 展示每天最高/最低温，两个系列均为 line。真实时效请求的全部渠道失败时，说明缺失或使用空 data，不用示例数值填充图表，即使标注“示例”也不满足该请求。示例数据仅用于用户明确要求演示组件的场景。

每城在围栏外提供实际查询 URL 的 Markdown 来源链接，返回的天气网格坐标不替换原查询坐标。摘要只描述实际日期与温度，不推断降温成因；天气现象只在工具提供明确文字或已核对天气码映射时补充。图例标明最高/最低温即可，具体线条颜色由前端随主题决定。

绘制每日最高/最低温时优先使用逐日预报字段。小时预报采样的最大/最小值只标为“采样最高/最低温”，缺少全天时段时在图卡中注明；未来的预报值不能称为实测值。

Image 使用 src 或 path 之一，alt 描述图片内容。src 接受无凭据的 HTTPS URL 或同源 `/...` 路径；不能用协议相对 URL、data/file/javascript URL。path 使用实际图片文件的沙盒绝对路径，例如 `{"type":"Image","path":"/home/user/workspace/projects/<project-dir>/generated-image.png","alt":"生成的图片"}`，沿用工具返回的真实路径。生成图片保存在当前 Project 工作目录；Home 或临时目录中的图片先保存到当前工作目录再展示。前端通过当前会话鉴权读取。新生成图片作为最终结果时，同时调用 `present_artifacts` 自动保存到“我的成果”；展示已有图片或检索参考图时无需登记。直接填文件路径，不拼接 API URL 或 Token，不使用路径跳转或宿主机路径。只使用已知存在的图片地址，不猜测路径；图片来源和详细说明放围栏外，简短说明可用 caption。尚未实际读取图片时，alt 和说明沿用用户描述，保留已知内容。单张图片默认保留原始宽高比，展示范围不超过正文宽度和 400px 高度；只有用户需要特定比例时才显式设置 aspectRatio，并按需要选择 cover 或 contain（默认）。ImageGroup 的缩略图统一使用 5:4 和 cover；点击后以完整比例预览，支持前后切换、方向键和 Esc 关闭。

## 字段与交互边界

输入组件放在 Form 内，name 使用字母开头的字母、数字或下划线，最多 40 字符，同一 Form 内不重复；保留字 constructor、prototype、__proto__ 不可用。Form 至少包含一个输入字段和一个 submit:true 的 Button。required、disabled、submit、pill、block 使用布尔值。文本输入最多 4000 字符。日期范围与默认值要彼此一致。

ListViewItem 保留已有 `{"type":"compose","text":"草稿内容"}` 动作，只追加输入框草稿，由用户自行发送；有动作的整行列表项内部只放非交互内容。它与 Form/Button 的直接发送不同，不用列表项代替表单提交。

单个文本最多 4000 字符，单个围栏最多 24000 字符、100 个节点、8 层。完整围栏闭合后展示；非法 JSON、未知组件、属性或动作显示错误。无法用当前组件清楚表达时使用普通 Markdown，复杂完整页面可作为独立交付物。
