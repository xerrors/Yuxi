# 无跳转的嵌入式登录

本节说明宿主系统已有登录态时，如何把 Yuxi 以 iframe 嵌入并完成登录，全程不出现重定向与二次登录页。

> 本文与 [第三方登录认证](./third-party-auth.md) 解决的是两类问题。OIDC 适合同一套身份体系下的独立部署，本方案适合宿主系统已经有会话、Yuxi 作为子系统出现在页面中的情形。

## 适用场景

- 宿主系统已有账号体系与登录页，Yuxi 只承担知识库、检索、智能体等能力
- Yuxi 以 iframe 出现在宿主的某个路由下，需要与宿主导航同时可见
- 不希望用户在 iframe 内再次输入账号密码，也不希望页面跳到身份提供方

## 与 OIDC 登录的区别

| 项目 | OIDC 登录 | 无跳转换票 |
| --- | --- | --- |
| 身份来源 | 身份提供方（IdP） | 宿主系统已有会话 |
| 浏览器行为 | 重定向到 IdP 再回调 | 无跳转，iframe 内完成 |
| 用户操作 | 首次需要登录 | 无感知 |
| 配置方式 | `OIDC_*` 环境变量 | 宿主令牌 + 一个换票接口 |
| 适用形态 | 独立部署 | 嵌入宿主页面 |

## 整体流程

1. 用户已登录宿主系统，浏览器持有宿主令牌
2. 宿主前端在加载 iframe 前，把令牌提交给 Yuxi 的换票接口
3. Yuxi 服务端把令牌转发给宿主的用户信息接口校验
4. 校验通过后，按用户身份生成稳定 uid，建立或复用本地用户
5. Yuxi 签发自己的 JWT 并写入 cookie，iframe 内直接进入工作区
6. 令牌失效时返回 401，宿主前端清理登录态并回到自己的登录页

## 服务端实现

### 1. 换票接口

在 `backend/server/routers/auth_router.py` 中新增一个接口，输入宿主令牌，输出 Yuxi 令牌。要点是校验与签发分离，宿主令牌只用于一次身份确认，不写入数据库。

```python
@auth.post("/host-sso", response_model=Token)
async def host_sso(payload: HostSSORequest, db: AsyncSession = Depends(get_db)):
    host_api_url = os.getenv("HOST_API_URL", "").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{host_api_url}/api/auth/profile",
                headers={"Authorization": f"Bearer {payload.token}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="host auth unavailable") from exc

    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="host session expired")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="host auth failed")

    profile = response.json().get("data") or {}
    identity = str(profile.get("email") or profile.get("username") or "").strip().lower()
    if not identity:
        raise HTTPException(status_code=502, detail="host profile has no identity")

    uid = "host_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    role = {"admin": "superadmin", "teacher": "admin", "student": "user"}.get(
        profile.get("role"), "user"
    )
    # 查 User，不存在则创建，并按需创建默认部门
    ...
    return create_access_token(...)
```

### 2. 状态码约定

上游服务不可用与用户身份失效必须区分，否则宿主无法判断是重试还是重新登录。

| 状态码 | 含义 | 宿主侧处理 |
| --- | --- | --- |
| 401 | 宿主令牌失效 | 清理登录态，回到宿主登录页 |
| 502 | 宿主返回的用户信息不合法 | 记录日志，提示联系管理员 |
| 503 | 宿主认证服务不可达 | 可重试，显示稍后再试 |

### 3. 用户与部门映射

uid 由身份标识哈希得到，同一个人重复换票得到同一个 uid，不会重复建号。角色映射放在服务端，不信任前端传入的角色字段。首次访问时按需创建用户与默认部门，后续只更新必要字段。

### 4. 安全要求

- 宿主令牌只在内存中转发一次，不落库、不写日志、不进响应
- 角色由服务端映射，前端传什么角色都不采信
- 换票接口建议限制来源网段，或要求宿主侧附带签名
- 错误响应不包含宿主接口的原始报文，避免泄露内部结构

## 前端嵌入

### 1. 布局参数

宿主通过查询参数加载 Yuxi，`embed=1` 表示嵌入模式：隐藏 Yuxi 自身侧栏与顶栏，只保留内容区，避免与宿主导航重复。

### 2. 单 iframe 持久化

宿主在同一路由树内切换子页面时，要给 iframe 一个稳定 key，不要因为路由变化销毁重建。重建会导致换票重复执行，也会让 Yuxi 内部的会话状态丢失。

### 3. 页面路由控制

宿主目录切到 Yuxi 的具体页面时，用受限的 `postMessage` 通知 iframe 内部跳转，消息体只允许白名单内的路由名，iframe 侧校验 `event.origin` 后再执行。

```js
// 宿主侧
iframe.contentWindow.postMessage({ type: 'host:navigate', target: 'search' }, yuxiOrigin);

// Yuxi 侧
window.addEventListener('message', (event) => {
  if (event.origin !== hostOrigin) return;
  if (event.data?.type !== 'host:navigate') return;
  if (!ALLOWED_TARGETS.includes(event.data.target)) return;
  router.push(targetToPath(event.data.target));
});
```

### 4. 令牌失效处理

iframe 内的接口返回 401 时，通过 `postMessage` 通知宿主，由宿主统一处理登录跳转，避免 iframe 内部出现登录页。

## 环境依赖

知识库相关路由只在完整模式下注册，嵌入前请确认：

```sh
LITE_MODE=false
```

精简模式不会注册 `/api/knowledge/*`，知识库列表与分块接口会返回 404。

## 检查清单

- [ ] 换票接口在宿主令牌无效时返回 401，不绕过 Yuxi 权限
- [ ] 同一身份重复换票得到同一个 uid
- [ ] `embed=1` 下宿主导航可见，Yuxi 侧栏隐藏
- [ ] 宿主目录切换不重复换票，iframe 不重建
- [ ] `postMessage` 校验 origin，且只接受白名单路由
- [ ] `LITE_MODE=false` 时知识库接口可用
