# 飞书知识库授权与检索阅读接入调研

调研日期：2026-10-16。本文面向 Yuxi 集成开发者，属于外部能力与配置的调研材料。前置知识是飞书应用、OAuth 和知识空间。目标是厘清授权粒度、检索与阅读边界，并核对 WeKnora 的实现。本文不声明飞书连接器已在 Yuxi 实现，不保存账号、租户标识、应用凭据或文档内容。

本页保留接入前的调查和建议，后续实现采用管理员配置用户 token 的共享连接，当前行为见[飞书只读连接器决定](../decisions/implemented/2026-10-16-feishu-readonly-connector.md)。下文“尚未实现”和“未取得用户 token”限定于本次调研。这不表示当前产品没有飞书连接能力。

## 授权能细到什么范围

飞书支持把一个知识空间或其中某个节点授权给应用。官方给出的整库方案有两种：将应用机器人所在群加入知识空间的可阅读成员，或由知识库管理员使用自己的 `user_access_token`，调用添加空间成员接口，把应用 `open_id` 加为成员。单节点可以通过“添加文档应用”授予协作权限。应用可用范围须覆盖相应资源所有者。群中添加的是应用机器人，不能替换成自定义机器人。只读接入使用可阅读角色即可。[官方知识库授权说明](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa)、[如何为应用开通云文档资源权限](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-add-permissions-to-app)

API 权限（scope）、实际调用身份和资源访问权共同限制结果。scope 决定可调用的能力；资源 ACL 决定该身份可读取哪些知识空间或文档。应用后台的“应用身份权限”和“用户身份权限”分别申请。开通 API 权限不会自动让应用读取全企业文档。[申请 API 权限](https://open.feishu.cn/document/ukTMukTMukTM/uQjN3QjL0YzN04CN2cDN)、[获取访问凭证](https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/get-access-token)

| 调用身份 | 资源范围由谁决定 | 适合的使用方式 |
| --- | --- | --- |
| `user_access_token` | 授权用户原有的可读写范围，并受其实际授予的 scope 限制 | 每个 Yuxi 用户绑定自己的飞书账号，在线检索和阅读 |
| `tenant_access_token` | 应用身份拥有的资源权限，知识空间或节点需给应用授权 | 无人值守同步明确授权给应用的共享资料 |

上表依据[访问凭证说明](https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/get-access-token)和[OAuth 用户令牌接口](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token-v3)。用户授权是代理该用户使用能力。它不是把该用户的全部组织角色复制给应用身份。按 `space_id` 或 `node_id` 缩小搜索范围属于调用参数约束。它本身不会把用户 OAuth 凭证的其他访问能力撤销。

“给外部系统接入”与“将内容公开到企业外”需要分别判断。Yuxi 作为服务端，可以持有本企业应用和用户授予的凭证调用 API。目标文档不需要因此改为互联网公开。若要分享给其他企业的成员，则文档、知识库、企业三层策略均可能限制对外分享。公开链接也不能代替 API 所需凭证及权限。[云文档权限常见问题](https://www.feishu.cn/hc/zh-CN/articles/360044508113-%E4%BA%91%E6%96%87%E6%A1%A3%E6%9D%83%E9%99%90%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98)、[知识库权限说明](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-add-permissions-to-app)

企业自建应用的常规发布和使用范围是同一企业；面向多个企业分发的官方路径是商店应用，由各企业安装并审核权限。不能据此推断“个人企业中创建一个应用，经任意公司的员工登录就能读取所有公司知识库”。跨企业外部协作、应用安装和 API 可见性要结合目标企业策略实测。[应用类型与开发流程](https://open.feishu.cn/document/home/introduction-to-custom-app-development/self-built-application-development-process)、[商店应用权限审核](https://open.feishu.cn/document/ukTMukTMukTM/uQjN3QjL0YzN04CN2cDN)

“员工在企业内创建的知识空间”与“飞书个人账号创建的知识库”需要区分。官方帮助明确后者不支持添加管理员或成员，不能直接套用企业空间的群成员授权路径。外部用户不能成为知识库管理员。将外部用户加入成员需要企业启用关联组织等条件。个人账号知识库的应用身份访问范围仍需对具体文档/API 验证，本文不据此推断整库应用授权可用。[设置知识库管理员和成员](https://www.feishu.cn/hc/zh-CN/articles/530997871518-%E8%AE%BE%E7%BD%AE%E5%BD%93%E5%89%8D%E7%9F%A5%E8%AF%86%E5%BA%93%E7%AE%A1%E7%90%86%E5%91%98%E5%92%8C%E6%88%90%E5%91%98)

## 基本检索与阅读支持到哪里

| 能力 | 官方接口与最小相关权限 | 已确认的边界 |
| --- | --- | --- |
| 搜索用户可见云文档 | `POST /open-apis/suite/docs-api/search/object`；用户身份 `search:docs:read` | 仅支持用户 token；每次最多 50 条，`offset + count < 200`；返回文档标识和标题，不返回 RAG 分块与向量分数；`docs_types=doc` 包括 doc 和 docx |
| 搜索 Wiki | 当前官方文档列出 `POST /open-apis/wiki/v2/nodes/search`；用户身份查看知识库权限 | 仅支持用户 token；`query` 最长 50 字符，单页最多 50；`space_id` 可限定空间，`node_id` 可限定节点及子树且须同时传 `space_id` |
| 列知识空间 | `GET /open-apis/wiki/v2/spaces`；`wiki:space:retrieve` 或 `wiki:wiki:readonly` | 支持用户及应用 token；不返回“我的文档库”；存在权限过滤后空页仍 `has_more=true` 的情况 |
| 解析 Wiki 节点 | `GET /open-apis/wiki/v2/spaces/get_node`；`wiki:node:read` 或 `wiki:wiki:readonly` | 调用身份需有节点阅读权；返回实际资源的 `obj_token`、`obj_type`；Wiki URL 中的节点 token 与正文文档 ID 不同 |
| 读新版文档正文 | `GET /open-apis/docx/v1/documents/:document_id/raw_content`；`docx:document:readonly` | 支持用户及应用 token；需要文档阅读权；纯文本不保留富文本样式，不能视为图片或附件的完整读取；单应用每秒 5 次 |

来源：[搜索云文档](https://open.feishu.cn/document/server-docs/docs/drive-v1/search/document-search)、[搜索 Wiki](https://open.feishu.cn/document/server-docs/docs/wiki-v2/search_wiki)、[获取知识空间列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/list)、[获取节点信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)、[获取文档纯文本内容](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content)。

Wiki 搜索和节点信息的 `obj_type` 契约不同。搜索文档给出整数枚举（Docx 为 `8`），节点信息给出 `docx` 等字符串。当前官方搜索文档使用 v2 URL，部分 SDK 或历史资料仍出现 v1。接入前须以真实请求核对，不把历史 URL 当作已验证可用路径。搜索接口的已公开响应不含 RAG 片段，官方说明也不足以证明其具备语义召回、正文全文覆盖或稳定相关性分数，因此基本能力应表述为“关键词查找文档，再读取正文”。[搜索 Wiki 契约](https://open.feishu.cn/document/server-docs/docs/wiki-v2/search_wiki)、[节点信息契约](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)

Wiki 可挂载新版文档、旧版文档、电子表格、多维表格、文件、思维笔记、幻灯片等资源。读取 Wiki 节点不等于读取全部类型的正文：新版文档可先用纯文本；电子表格、多维表格需分别接入范围读取和记录查询；图片、附件及富文本块需另行处理。首个基本版本以 docx 正文为验收范围，并显式返回其他类型的限制，复杂类型按当前需求逐一增加。[知识库内容读取说明](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa)

## 需要取得与核对的配置

用户 OAuth 接入需要 App ID、服务端保存的 App Secret、与实际授权入口一致的重定向 URL、用户已授予的 scope、用户 token 和有效期。正式持续接入还需要 `offline_access` 和刷新流程。当前官方推荐 token 端点为 `https://accounts.feishu.cn/oauth/v3/token`；v2 获取 token 文档已标为历史版本。`refresh_token` 仅能使用一次，刷新时需保存返回的新值，不能硬编码 token 生命周期。[获取授权码](https://open.feishu.cn/document/common-capabilities/sso/api/obtain-oauth-code)、[获取用户 token v3](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token-v3)、[刷新用户 token](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/refresh-user-access-token)

| 配置类别 | 基本版本所需内容 | 处理位置 |
| --- | --- | --- |
| 应用凭据 | App ID、App Secret；所在飞书/Lark 区域 | 服务端密钥配置；不写研究报告或代码仓库 |
| OAuth 回调 | 开发和部署入口所用回调必须与飞书安全设置一致 | 身份模块负责发起授权、校验 state 和绑定 Yuxi 用户 |
| 只读 scope | 已有 `search:docs:read`；Wiki 检索及节点读取补 `wiki:wiki:readonly`；docx 正文补 `docx:document:readonly` | 飞书应用的用户身份权限，用户授权时请求相同 scope |
| 持续访问 | `offline_access`、token 有效期、单次使用的 refresh token 轮换 | 服务端用户凭证存储；撤销或过期应重新授权 |
| 检索边界 | 目标 `space_id`；需要节点子树限制时加 `node_id` | 知识库连接配置；不把检索参数误写成飞书 OAuth 资源授权 |
| 应用身份方案 | 应用机器人或 open_id，以及知识空间/节点 ACL | 仅在选择无人值守应用身份时配置；在线用户身份方案无须借用管理员 token |

scope 和接口对应关系见上一节；回调与用户授权边界见[授权码说明](https://open.feishu.cn/document/common-capabilities/sso/api/obtain-oauth-code)。表中 Yuxi 职责是接入建议，尚未实现。

已使用用户现有登录会话只读核查 Yuxi-Dev 开发者后台：App ID 可见；App Secret 被界面遮蔽，未揭示或导出。当前权限配置中可见用户身份 `search:docs:read`、应用身份 `tenant:tenant:readonly`，界面状态均为 Added。后者用于租户信息读取。它不构成知识文档授权。安全设置已配置开发 OAuth 回调，实际地址未写入材料，需与最终使用入口核对。版本页面有 `1.0.1 Released`。应用顶部同时显示 Pending release，因此必须区分已发布版本与待发布的配置，不能把待发布权限当作线上生效证据。

同一用户会话的云文档页面还可见一个企业知识空间，已从空间设置取得目标 `space_id`；实际 ID 未写入材料。成员设置页面显示当前用户为该空间 Admin，实际用户名未保存。该空间安全设置的“允许页面对外分享”处于选中状态。这只证明空间策略允许对外分享。它不证明其文档已经公开，也不证明应用或 API token 获得了访问权。

上述后台状态是 2026-10-16 的页面观察。它不代表真实调用验证。未取得用户 token 或应用资源 ACL；未执行真实搜索、正文读取或撤权验证；没有发布应用、扩大权限或修改分享设置。企业管理员审批、目标企业允许的 scope、目标资源实际可访问性仍需真实链路确认。

## WeKnora 实际如何支持飞书

核对对象为 Tencent/WeKnora `main`，调研时 HEAD 为 `2fbfb4471581cd5e7d731bc7a2d443fe7d8e95b5`。以下按连接器源码确认，不能把注册表中的 `oauth2` 分类当成个人用户 OAuth 已实现。

WeKnora 的飞书 Wiki 连接器要求 `app_id` 和 `app_secret`，通过 `/auth/v3/tenant_access_token/internal` 获取应用身份 token，并缓存及提前刷新。当前连接器未使用用户授权码或个人 refresh token。凭据获取和配置校验由 [core/client.go](https://github.com/Tencent/WeKnora/blob/main/internal/datasource/connector/feishu/core/client.go) 与 [core/shared.go](https://github.com/Tencent/WeKnora/blob/main/internal/datasource/connector/feishu/core/shared.go) 拥有。

资源选择按空间、顶层节点、子节点逐层懒加载。同步时递归遍历选择范围，支持全量、增量和可恢复流式抓取，增量依据编辑时间与游标。这是“从飞书摄取文档进入 WeKnora 本地知识库”的同步路径，当前源码没有将用户问题直接交给飞书搜索 API 的实时检索路径。[Wiki 连接器](https://github.com/Tencent/WeKnora/blob/main/internal/datasource/connector/feishu/wiki/connector.go)

其正文抓取默认将 doc/docx 异步导出为 docx，sheet/bitable 导出为 xlsx，普通文件下载原文件后交给本地解析。docx 可切换 `FEISHU_DOCX_PARSE_MODE=blocks`，通过 blocks 转 Markdown 并处理附件；API 失败或转换为空时回退导出。该模式服务于同步摄取，复杂度超出基本在线阅读所需。[Wiki 抓取实现](https://github.com/Tencent/WeKnora/blob/main/internal/datasource/connector/feishu/wiki/connector.go)、[共享正文抓取](https://github.com/Tencent/WeKnora/blob/main/internal/datasource/connector/feishu/core/shared.go)、[官方数据源说明](https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/10-datasource.md)、[官方飞书云盘说明](https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/24-feishu-drive.md)

可参考它的资源树选择、Wiki token 解析、分页、token 缓存和按类型抓取。首个在线检索阅读版本只需复用边界处理经验。它无须引入同步调度与本地索引。若后续采用应用身份同步，飞书中原有的每个终端用户 ACL 不能靠一个共享应用 token 自动保留，Yuxi 必须定义导入后的访问范围与撤权语义。此处为基于身份模型的接入风险判断。它并非对 WeKnora 完整权限系统的审计结论。

## 对 Yuxi 的接入判断与待验证项

现有 [DifyKB](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/knowledge/implementations/dify.py) 提供外部只读检索的配置与请求模式，但 Dify 的分块和分数契约不能直接复制给飞书文档搜索。飞书用户凭据须归属于绑定的 Yuxi 用户。不能把本次登录用户的 token 保存为所有 Yuxi 用户共享的知识库访问凭证。首个实现可沿用现有知识库检索与阅读入口，在线调用用户身份搜索，并对 docx 提供纯文本阅读。

实现前需确认：选用全云文档搜索还是限定空间的 Wiki 搜索；目标用户完成最小 scope 授权；真实请求验证官方 Wiki v2 URL；读取时重新检查当前用户与空间边界；撤销用户授权或资源权限后明确拒绝。最小验收应覆盖目标空间内可读 docx 搜索并读取正文，以及空间外、无阅读权、token 失效、非 docx 类型、限频和分页结果。当前这些 API 验证均为 Not run，原因是尚未取得用户 token。

材料收集方式：浏览官方飞书文档与帮助中心；对空 HTML 抽取页面读取其声明的 `.md` 替代版本；浅克隆 Tencent/WeKnora 并检查上述源码；只读核查已有用户浏览器会话中的应用与空间配置。正文引用为可公开访问的一手来源，账号配置仅保留不含标识及凭据的状态。
