# 开发阶段
FROM node:26-alpine AS development
WORKDIR /app
ENV TZ=Asia/Shanghai

# 安装 pnpm（版本与 package.json 的 packageManager 字段对齐，避免 pnpm@latest 与旧 lockfile 不兼容）
RUN npm install -g pnpm@10.11.0

# 复制 package.json 和 pnpm-lock.yaml
COPY ./web/package*.json ./
COPY ./web/pnpm-lock.yaml* ./

# 安装依赖（--frozen-lockfile 保证 dev 与 build/CI 三处依赖与 pnpm-lock.yaml 一致，避免漂移）
RUN pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com

# 复制源代码
COPY ./web .

# 暴露端口
EXPOSE 5173

# 启动开发服务器的命令在 docker-compose 文件中定义

# 生产阶段
FROM node:26-alpine AS build-stage
WORKDIR /app

# 安装 pnpm
RUN npm install -g pnpm@10.11.0

# 复制依赖文件
COPY ./web/package*.json ./
COPY ./web/pnpm-lock.yaml* ./

# 安装依赖
RUN pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com

# 复制源代码并构建
COPY ./web .
RUN pnpm run build

# 生产环境运行阶段
FROM nginx:alpine AS production
COPY --from=build-stage /app/dist /usr/share/nginx/html
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} \; \
    && find /usr/share/nginx/html -type f -exec chmod 644 {} \;
COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./docker/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
