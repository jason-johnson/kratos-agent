# syntax=docker/dockerfile:1.7

ARG NODE_IMAGE=node:20.19.5-bookworm-slim
ARG NGINX_IMAGE=nginx/nginx-unprivileged:1.30.6-alpine@sha256:2daef144b574804198caccc4041819fa1801ecd5df2252a39646bfa2b1ff3bea

FROM ${NODE_IMAGE} AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc,required=true \
    npm ci --ignore-scripts --no-audit --no-fund

COPY . .
RUN npm run build
RUN node generate-csp-config.mjs nginx.conf out /tmp/default.conf

FROM ${NGINX_IMAGE}

USER root

RUN apk upgrade --no-cache

USER 101

COPY --from=build /tmp/default.conf /etc/nginx/conf.d/default.conf
COPY runtime-config.template.json /opt/kratos/runtime-config.template.json
COPY --chmod=0555 40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
COPY --from=build /app/out/ /usr/share/nginx/html/

EXPOSE 8080
