# 常见问题

以下是 Yuxi 在安装和使用过程中最常见的问题及其解决方案。

## Docker 与启动问题

### 镜像拉取或构建失败

**镜像拉取问题**：

```bash
# Linux/macOS
bash scripts/pull_image.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/pull_image.ps1
```

**构建失败问题**：

如果配置了代理仍然失败，尝试以下步骤：

1. 注释 `api.Dockerfile` 中的代理配置
2. 注释 `docker-compose.yml` 中的代理构建参数
3. 添加国内镜像源加速：

```dockerfile
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 服务启动失败

1. 检查端口占用：`lsof -i :5050` 或 `netstat -tuln | grep 5050`
2. 确认 Docker 服务状态
3. 查看日志定位问题：
   ```bash
   docker logs --tail=100 api-dev
   docker logs --tail=100 web-dev
   ```

### 数据库服务问题

**Milvus / Neo4j 启动失败**：

```bash
# 重启服务
docker compose up milvus -d && docker restart api-dev
```

**Neo4j 连接信息**：
- 用户名：neo4j
- 密码：0123456789
- 管理界面：http://localhost:7474

### 账号相关问题

**首次运行创建管理员**：

Web 首次启动会引导初始化。也可以通过 API 创建：

```bash
# 检查是否首次运行
GET /api/auth/check-first-run

# 初始化管理员账号
POST /api/auth/initialize
# Body: {"user_id": "your_username", "password": "your_password"}
```

### 日志查看

```bash
# 查看所有容器状态
docker ps

# 查看实时日志
docker logs api-dev -f
docker logs web-dev -f

# 查看所有服务日志
docker compose logs --tail=100
```

## 功能使用问题

### OCR 服务不可用

- **RapidOCR**：确保 `MODEL_DIR/SWHL/RapidOCR` 下存在 `PP-OCRv4` 模型
- **MinerU / PP-Structure-V3**：检查 GPU 和 CUDA 版本是否兼容

### 登录失败被锁定

多次登录失败会临时锁定账户，请根据页面提示等待后重试。

---

如果以上问题无法解决你的问题，欢迎在 GitHub Issues 中提问。
