# 前端构建 + nginx SPA/反代（/api /sub 同源转发到 api:8443）
FROM node:22-alpine AS build
WORKDIR /app
# 1GB 内存 VPS 上 vite 默认 ~490MB 堆会 OOM；swap 加持下放宽堆上限
ENV NODE_OPTIONS=--max-old-space-size=2048
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build-only

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
