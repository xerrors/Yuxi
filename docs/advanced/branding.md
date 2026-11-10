# 自定义品牌信息

Yuxi 的品牌配置分为两部分：后端读取的站点信息，以及前端源码中的主题样式。前者可以通过 YAML 文件替换名称、Logo 和协议链接；后者需要修改前端资源。

## 配置站点信息

### 1. 创建本地配置

复制模板。`info.local.yaml` 通常是本地未跟踪文件；如果目标文件已经存在，不要覆盖它，直接编辑或先备份：

```bash
cp -n backend/yuxi/modules/system/static/info.template.yaml \
  backend/yuxi/modules/system/static/info.local.yaml
```

在 `info.local.yaml` 中修改：

```yaml
branding:
  name: "Example App"
  logo: "/logo.svg"
  title: "让团队知识更容易被使用"
  login_bg: ""

footer:
  copyright: "© Example App"
  user_agreement_url: "/protocols/user-agreement.template.html"
  privacy_policy_url: "/protocols/privacy-policy.template.html"
```

产品名称和 Logo 统一由 `branding.name`、`branding.logo` 拥有，首页、登录页和侧栏共用这两个字段。`title` 用于首页主标题。`login_bg` 留空时，登录页使用内置山茶折枝 SVG，颜色随当前主题变化，枝条缓慢摆动，花瓣缓慢飘落。填写图片地址时，左侧使用该图片；历史默认地址 `/login-bg.jpg` 无法加载时使用内置图，路径下已有的自定义图片仍会显示。页面直接读取 `branding`，名称填写最终产品名称；`organization`、`avatar`、`subtitle` 和 `subtitles` 不属于当前品牌字段。

图片和协议页面放在 `frontend/public` 下，路径从网站根目录开始写，例如 `/logo.svg`。Compose 中 API 的工作目录是 `/app`，因此默认配置路径可以写成：

```bash
YUXI_BRAND_FILE_PATH=yuxi/modules/system/static/info.local.yaml
```

也可以在环境文件中指定 API 容器内的绝对路径，并通过 Compose 只读挂载该文件。路径不存在时读取 `info.template.yaml`，两个 YAML 文件不合并；YAML 读取或解析失败时接口返回空配置。修改后既要核对 `/api/system/info` 的实际字段，也要刷新页面检查名称、图片和协议链接。

### 2. 重新加载

品牌接口每次请求读取 YAML。开发环境的 API 直接挂载 `backend/yuxi`，修改 YAML 后刷新浏览器页面即可读取新配置；前端在同一个页面会话内缓存已成功读取的品牌信息。

生产 Compose 不挂载仓库源码，品牌 YAML 会在构建 API 镜像时复制进去。修改 `backend/yuxi/modules/system/static/info.local.yaml` 后，需要重新构建并创建 API/worker 容器：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml \
  up -d --build --force-recreate api worker
```

如果使用仓库之外的品牌文件，需要在 Compose 覆盖配置中把它只读挂载到 API 容器，并让 `YUXI_BRAND_FILE_PATH` 指向容器内路径；修改该环境变量后同样要重新创建容器。页面会通过公开的 `/api/system/info` 读取站点信息，版本占位符 `{{YUXI_VERSION}}` 由 API 替换为当前版本。

## 登录协议

当 `footer.user_agreement_url` 和 `footer.privacy_policy_url` 同时有值时，登录和初始化页面显示协议勾选项；任一链接为空时，不显示勾选项。用户未勾选时，登录或初始化会被页面拦截并提示先同意协议。

仓库提供了两个模板：

- `frontend/public/protocols/user-agreement.template.html`
- `frontend/public/protocols/privacy-policy.template.html`

可以直接替换模板内容和其中的 `{{ORG_NAME}}`、`{{PRODUCT_NAME}}`、`{{EFFECTIVE_DATE}}` 等占位符，也可以把配置指向自定义的站内或外部页面。正式上线前请让法务审核协议文本。

## 修改主题样式

主题色变量位于：

- `frontend/src/assets/css/base.css`：浅色模式；
- `frontend/src/assets/css/base.dark.css`：暗色模式；
- `frontend/src/shared/model/theme.js`：主题选择器的默认配置。

优先修改已有 CSS 变量，不要在组件中散落新的硬编码颜色。当前主题的主要变量包括 `--main-1000`、`--main-900` 和 `--main-color`；`frontend/src/shared/model/theme.js` 的 `colorPrimary` 在运行时读取 `--main-color`，因此调整 `base.css` 与 `base.dark.css` 中的 `--main-color` 与 `--main-*` 色阶即可，不需要再修改 `theme.js`。`--main-color` 在浅色模式下引用 `--main-700`，暗色模式单独定义该值；hover、active、link 和 focus 等 token 还使用 `--main-600`、`--main-800`、`--main-900` 和 `--main-100`，需要一起保持一致。新增颜色时，同时检查浅色、暗色、hover、focus、禁用和错误状态的对比度。

开发环境会通过 Vite 热更新样式；生产环境需要重新构建 Web 镜像：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  up -d --build frontend
```

品牌 YAML、主题颜色和图标属于不同配置面：YAML 影响站点信息接口，CSS 和 `theme.js` 影响前端资源。修改其中一项不会自动改动另一项。
