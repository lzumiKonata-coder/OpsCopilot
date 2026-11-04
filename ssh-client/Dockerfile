# ==========================================
# OpsCopilot Web Client (Frontend) Dockerfile
# ==========================================

# 阶段 1：构建产物 (基于 Debian glibc 的标准 Node.js 环境)
FROM node:20-slim AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# 阶段 2：Nginx 生产发布 (基于官方 Debian glibc 镜像，杜绝 Alpine musl 的 pwritev2 权限错误)
FROM nginx:1.26 AS runner

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
