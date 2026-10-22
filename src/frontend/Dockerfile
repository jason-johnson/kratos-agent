# syntax=docker/dockerfile:1.7

ARG NODE_IMAGE=node:20.19.5-bookworm-slim
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.29.5-alpine

FROM ${NODE_IMAGE} AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc,required=true \
    npm ci --ignore-scripts --no-audit --no-fund

COPY . .
RUN npm run build

FROM ${NGINX_IMAGE}

USER root

RUN apk upgrade --no-cache

USER 101

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY runtime-config.template.json /opt/kratos/runtime-config.template.json
COPY --chmod=0555 40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
COPY --from=build /app/out/ /usr/share/nginx/html/

EXPOSE 8080
