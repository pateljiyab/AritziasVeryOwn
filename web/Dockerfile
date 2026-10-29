# The web client, built once and served by nginx, which also proxies /api to the API service.
# Build from web/: docker compose build (or docker build web)
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Empty: the client calls /api/v1 on its own origin, and nginx forwards it.
ENV VITE_API_BASE=""
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
