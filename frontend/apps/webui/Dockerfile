# syntax=docker/dockerfile:1

# Build the OSS browser SPA from the pnpm workspace, then serve it with nginx.
# The build context must be the repository root:
#
#   docker build -f frontend/apps/webui/Dockerfile -t valuz-oss-webui .

ARG NODE_IMAGE=node:24-slim
ARG NGINX_IMAGE=nginx:1.27-alpine

FROM ${NODE_IMAGE} AS build

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH
RUN corepack enable

WORKDIR /repo/frontend

# Copy workspace metadata first so dependency installation remains cached when
# application source changes. Include every transitive @valuz workspace package
# used by the WebUI.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/turbo.json ./
COPY frontend/apps/webui/package.json ./apps/webui/package.json
COPY frontend/packages/app/package.json ./packages/app/package.json
COPY frontend/packages/core/package.json ./packages/core/package.json
COPY frontend/packages/genui-blocks/package.json ./packages/genui-blocks/package.json
COPY frontend/packages/parser-plugins/package.json ./packages/parser-plugins/package.json
COPY frontend/packages/shared/package.json ./packages/shared/package.json
COPY frontend/packages/ui/package.json ./packages/ui/package.json

RUN pnpm install --frozen-lockfile --filter @valuz/webui...

WORKDIR /repo
COPY frontend ./frontend
COPY i18n ./i18n

# The production image proxies this same-origin path to the backend. Keeping the
# browser on one origin avoids per-host rebuilds and cross-origin configuration.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

WORKDIR /repo/frontend
RUN pnpm --filter @valuz/webui build

FROM ${NGINX_IMAGE} AS serve

LABEL org.opencontainers.image.title="valuz-oss-webui" \
      org.opencontainers.image.description="Valuz OSS browser WebUI"

ENV BACKEND_URL=http://backend:8000

# The official nginx entrypoint renders templates with environment variables at
# container startup, so BACKEND_URL can be overridden without rebuilding.
COPY frontend/apps/webui/conf/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /repo/frontend/apps/webui/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
