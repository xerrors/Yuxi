# 使用命令行工具

`yuxi-cli` 是 Yuxi 的命令行客户端。它可以保存多个实例地址、登录远程实例、上传和查询知识库文件，并启动一个只在本机监听的临时聊天页面。

## 安装

推荐使用 `uv` 或 `pipx` 安装：

```bash
uv tool install yuxi-cli
```

只想试用一次时，可以直接运行：

```bash
uvx --from yuxi-cli yuxi --help
```

确认安装成功：

```bash
yuxi --version
```

## 连接实例

先保存实例地址，再选择当前实例：

```bash
yuxi remote add local http://localhost:5173
yuxi remote use local
yuxi remote ping
```

CLI 会把配置保存到 `~/.yuxi/config.toml`。它会把实例地址标准化为入口地址，并在请求时使用对应的 `/api` 路径；不要把 URL 直接写成某个具体 API 路径。

同时管理多个实例时，重复执行 `remote add`，再用下面的命令切换：

```bash
yuxi remote list
yuxi remote use production
```

## 登录和退出

浏览器登录会打开一个授权页面：

```bash
yuxi login --browser
```

服务器或不能自动打开浏览器时，使用 `--no-open`，再手动打开终端输出的地址：

```bash
yuxi login --browser --no-open
```

也可以导入已经在 Yuxi 中创建的 API Key：

```bash
yuxi login --api-key yxkey_<your-key>
```

常用的状态命令：

```bash
yuxi whoami
yuxi status
yuxi logout
```

默认退出会同时撤销通过 CLI 创建的 API Key，并清除本地凭证。只清除本地文件、不撤销远程密钥时使用：

```bash
yuxi logout --local-only
```

API Key secret 会在创建或安全幂等重放响应中返回。不要把它写入 Shell 历史、代码仓库或公开日志；生产环境请使用 HTTPS 连接实例。

## 启动本地聊天页面

先完成 CLI 登录，再运行：

```bash
yuxi chat
```

CLI 会启动一个临时 HTTP 服务，只监听 `127.0.0.1` 的随机端口，并尝试打开浏览器。页面通过 CLI 代理当前实例的 Agent 请求，API Key 保留在 CLI 进程中，不会发送到浏览器。

指定智能体或不自动打开浏览器：

```bash
yuxi chat --agent-slug my-agent
yuxi chat --remote production --no-open
```

关闭终端中的进程后，本地页面也会停止。当前页面支持纯文本对话、新建会话、`/state` 查看线程状态和 `/approve` 继续工具审批；附件和 `ask_user_question` 仍需使用正式 Web 界面。

## 查看可用 Agent

列出当前账号有权调用的主 Agent：

```bash
yuxi agent list
```

列表中的 `*` 表示默认 Agent。使用 slug 查看服务端已授权返回的详细配置，包括绑定的模型、Skills、系统提示词、工具、MCP、知识库和子 Agent：

```bash
yuxi agent show default-chatbot
```

未显式绑定的工具、知识库、Skills 和子 Agent 显示为“默认（全部可用）”；MCP 未显式添加时不直接加载，激活 Skill 后仍可加载其 MCP 依赖。`tools`、`knowledges`、`mcps` 和 `skills` 的显式空列表显示为“无”；`subagents` 的空列表仍按服务端契约显示为“默认（全部可用）”。这两条命令都支持 `--remote <name>` 切换实例，以及 `--json` 输出完整服务端响应。

## 上传知识库文件

上传需要当前账号可以管理知识库。省略 `--kb-id` 时，CLI 会列出当前实例中支持文档上传的知识库供选择：

```bash
yuxi kb upload ./docs
```

指定知识库并控制文件类型和并发数：

```bash
yuxi kb upload ./docs --kb-id <kb-id> --concurrency 4
yuxi kb upload ./docs --include-ext md,html,docx
yuxi kb upload ./docs --exclude-ext pdf,png
```

默认选择 `.md`、`.txt`、`.docx`、`.html` 和 `.htm`。PDF、图片等类型需要显式选择，并且后续仍要在知识库页面配置解析和索引。单个文件不能超过 100 MB；`--concurrency` 支持 1–300，默认 10。

上传命令负责把文件上传到知识库暂存区并添加文件记录，不代替解析和向量入库。完成后回到知识库详情页，确认文件状态并继续处理。相同内容的文件会按服务端结果显示为已上传过；`--force-upload-file` 只跳过 CLI 的文件名预检查，不能绕过服务端校验。

## 查询知识库

登录用户可以查询自己有读取权限的知识库：

```bash
yuxi kb list
yuxi kb files --kb-id <kb-id>
yuxi kb files --kb-id <kb-id> --query handbook --status indexed
yuxi kb query --kb-id <kb-id> "如何申请年假？"
```

先用 `files` 找到文件 ID，再打开解析后的 Markdown 或在文件内查找：

```bash
yuxi kb open --kb-id <kb-id> --file-id <file-id>
yuxi kb find --kb-id <kb-id> --file-id <file-id> --pattern "年假"
yuxi kb find --kb-id <kb-id> --file-id <file-id> --pattern "年假[：:][ ]*\\d+" --regex
```

`kb files` 的 `--query` 只匹配文件名，不搜索文件内容。`kb query` 返回检索片段；`kb open` 的 `--limit` 最大为 1800 行；需要脚本处理原始 JSON 时，给命令加 `--json`。

## 运行智能体评估

如果实例已配置 Langfuse 数据集，并且本机环境可以读取对应的 Langfuse 变量，可以运行：

```bash
yuxi agent eval \
  --dataset-name demo-dataset \
  --agent-slug default-chatbot \
  --experiment-name cli-demo
```

命令使用当前 remote 的登录态调用 Yuxi，并把每条样例的结果写回 Langfuse experiment。CLI 不负责创建数据集；数据集管理和评估边界见[智能体评估](../agents/agent-evaluation.md)。
