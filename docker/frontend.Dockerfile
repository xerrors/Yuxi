# 开发阶段
FROM node:22-alpine AS development
WORKDIR /app
ENV TZ=Asia/Shanghai

# 安装 pnpm（版本与 package.json 的 packageManager 字段对齐，避免 pnpm@latest 与旧 lockfile 不兼容）
RUN npm install -g pnpm@11.24.0

# 复制 pnpm 依赖声明与锁文件
COPY --chown=0:0 --chmod=0644 ./frontend/package*.json ./
COPY --chown=0:0 --chmod=0644 ./frontend/pnpm-lock.yaml* ./
COPY --chown=0:0 --chmod=0644 ./frontend/pnpm-workspace.yaml ./
COPY --chown=0:0 ./frontend/patches ./patches

# 安装依赖（--frozen-lockfile 保证 dev 与 build/CI 三处依赖与 pnpm-lock.yaml 一致，避免漂移）
RUN pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com

# 复制源代码
COPY --chown=0:0 ./frontend .
# 仅规范化项目文件，保留包管理器安装的可执行依赖。
RUN find /app -path /app/node_modules -prune -o -type d -exec chmod 0755 {} + \
    && find /app -path /app/node_modules -prune -o -type f -exec chmod 0644 {} +

# 暴露端口
EXPOSE 5173

# 启动开发服务器的命令在 docker-compose 文件中定义

# 生产阶段
FROM node:22-alpine AS build-stage
WORKDIR /app

# 安装 pnpm
RUN npm install -g pnpm@11.24.0

# 复制依赖文件
COPY --chown=0:0 --chmod=0644 ./frontend/package*.json ./
COPY --chown=0:0 --chmod=0644 ./frontend/pnpm-lock.yaml* ./
COPY --chown=0:0 --chmod=0644 ./frontend/pnpm-workspace.yaml ./
COPY --chown=0:0 ./frontend/patches ./patches

# 安装依赖
RUN pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com

# 复制源代码并构建
COPY --chown=0:0 ./frontend .
# 仅规范化项目文件，保留包管理器安装的可执行依赖。
RUN find /app -path /app/node_modules -prune -o -type d -exec chmod 0755 {} + \
    && find /app -path /app/node_modules -prune -o -type f -exec chmod 0644 {} +
RUN pnpm run build

# 生产环境运行阶段
FROM nginx:alpine AS production
COPY --from=build-stage --chown=0:0 /app/dist /usr/share/nginx/html
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} \; \
    && find /usr/share/nginx/html -type f -exec chmod 644 {} \;
COPY --chown=0:0 --chmod=0644 ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --chown=0:0 --chmod=0644 ./docker/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
