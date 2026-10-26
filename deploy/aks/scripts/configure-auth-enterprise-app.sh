#!/usr/bin/env bash
set -euo pipefail

: "${ENTRA_AUTH_CLIENT_ID:?Set ENTRA_AUTH_CLIENT_ID to the login application client ID.}"
: "${AKS_OPERATOR_PRINCIPAL_ID:?Set AKS_OPERATOR_PRINCIPAL_ID to the approved operator group object ID.}"

uuid_pattern='^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'
if [[ ! "$ENTRA_AUTH_CLIENT_ID" =~ $uuid_pattern ]]; then
  echo "ENTRA_AUTH_CLIENT_ID must be a UUID." >&2
  exit 2
fi
if [[ ! "$AKS_OPERATOR_PRINCIPAL_ID" =~ $uuid_pattern ]]; then
  echo "AKS_OPERATOR_PRINCIPAL_ID must be a UUID." >&2
  exit 2
fi

request_body="$(mktemp)"
response_body="$(mktemp)"
error_body="$(mktemp)"
trap 'rm -f "$request_body" "$response_body" "$error_body"' EXIT
chmod 600 "$request_body" "$response_body" "$error_body"

service_principal_id="$(
  az ad sp list \
    --filter "appId eq '$ENTRA_AUTH_CLIENT_ID'" \
    --query '[0].id' \
    --output tsv
)"

if [ -z "$service_principal_id" ]; then
  python - "$ENTRA_AUTH_CLIENT_ID" > "$request_body" <<'PY'
import json
import sys

print(json.dumps({
    "appId": sys.argv[1],
    "appRoleAssignmentRequired": True,
}))
PY

  for attempt in $(seq 1 18); do
    if az rest \
      --method post \
      --url https://graph.microsoft.com/v1.0/servicePrincipals \
      --headers Content-Type=application/json \
      --body "@$request_body" \
      --query id \
      --output tsv > "$response_body" 2> "$error_body"; then
      service_principal_id="$(cat "$response_body")"
      break
    fi
    if [ "$attempt" -eq 18 ]; then
      echo "The enterprise application could not be created after waiting for ownership propagation." >&2
      cat "$error_body" >&2
      exit 3
    fi
    sleep 10
  done
else
  printf '{"appRoleAssignmentRequired":true}\n' > "$request_body"
  az rest \
    --method patch \
    --url "https://graph.microsoft.com/v1.0/servicePrincipals/$service_principal_id" \
    --headers Content-Type=application/json \
    --body "@$request_body" \
    --output none
fi

az rest \
  --method get \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$service_principal_id/appRoleAssignedTo" \
  --output json > "$response_body"

assignment_count="$(
  python - "$AKS_OPERATOR_PRINCIPAL_ID" "$service_principal_id" "$response_body" <<'PY'
import json
import sys

principal_id, resource_id, path = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    assignments = json.load(stream).get("value", [])
print(sum(
    assignment.get("principalId") == principal_id
    and assignment.get("resourceId") == resource_id
    for assignment in assignments
))
PY
)"

if [ "$assignment_count" -eq 0 ]; then
  python - "$AKS_OPERATOR_PRINCIPAL_ID" "$service_principal_id" > "$request_body" <<'PY'
import json
import sys

print(json.dumps({
    "principalId": sys.argv[1],
    "resourceId": sys.argv[2],
    "appRoleId": "00000000-0000-0000-0000-000000000000",
}))
PY
  az rest \
    --method post \
    --url "https://graph.microsoft.com/v1.0/servicePrincipals/$service_principal_id/appRoleAssignedTo" \
    --headers Content-Type=application/json \
    --body "@$request_body" \
    --output none
elif [ "$assignment_count" -ne 1 ]; then
  echo "Unexpected duplicate operator-group assignments were found." >&2
  exit 4
fi

echo "Enterprise application configured with assignment-required access."
