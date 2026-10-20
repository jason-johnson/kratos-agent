#!/bin/sh
set -eu

if [ "${SKIP_RUNTIME_CONFIG_WRITE:-false}" = "true" ]; then
  exit 0
fi

envsubst '${API_URL} ${OBO_CLIENT_ID} ${AZURE_TENANT_ID} ${OBO_MCP_SCOPE} ${OBO_MCP_SERVER_NAME}' \
  < /opt/kratos/runtime-config.template.json \
  > /usr/share/nginx/html/config.json
