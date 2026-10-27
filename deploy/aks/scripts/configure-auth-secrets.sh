#!/usr/bin/env bash
set -euo pipefail

: "${ENTRA_AUTH_CLIENT_ID:?Set ENTRA_AUTH_CLIENT_ID to the login application client ID.}"
: "${KEY_VAULT_RESOURCE_ID:?Set KEY_VAULT_RESOURCE_ID to the target Key Vault resource ID.}"

credential_name="${AUTH_CREDENTIAL_NAME:-kratos-oauth2-proxy}"
client_id_secret_name="${AUTH_CLIENT_ID_SECRET_NAME:-kratos-oauth-client-id}"
client_secret_secret_name="${AUTH_CLIENT_SECRET_SECRET_NAME:-kratos-oauth-client-secret}"
cookie_secret_name="${AUTH_COOKIE_SECRET_NAME:-kratos-oauth-cookie-secret}"

uuid_pattern='^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'
vault_pattern='^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.KeyVault/vaults/[^/]+$'
secret_name_pattern='^[0-9A-Za-z-]{1,127}$'

if [[ ! "$ENTRA_AUTH_CLIENT_ID" =~ $uuid_pattern ]]; then
  echo "ENTRA_AUTH_CLIENT_ID must be a UUID." >&2
  exit 2
fi
if [[ ! "$KEY_VAULT_RESOURCE_ID" =~ $vault_pattern ]]; then
  echo "KEY_VAULT_RESOURCE_ID must identify an Azure Key Vault." >&2
  exit 2
fi
for secret_name in "$client_id_secret_name" "$client_secret_secret_name" "$cookie_secret_name"; do
  if [[ ! "$secret_name" =~ $secret_name_pattern ]]; then
    echo "Key Vault secret names must contain only letters, numbers, and hyphens." >&2
    exit 2
  fi
done

work_dir="$(mktemp -d)"
chmod 700 "$work_dir"
new_credential_id=""
credential_stored=false

remove_password() {
  local credential_id="$1"
  python - "$credential_id" > "$work_dir/remove-password.json" <<'PY'
import json
import sys

print(json.dumps({"keyId": sys.argv[1]}))
PY
  az rest \
    --method post \
    --url "https://graph.microsoft.com/v1.0/applications/$application_object_id/removePassword" \
    --headers Content-Type=application/json \
    --body "@$work_dir/remove-password.json" \
    --output none
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ -n "$new_credential_id" ] && [ "$credential_stored" = false ]; then
    remove_password "$new_credential_id" || echo "Failed to remove the uncommitted login credential." >&2
  fi
  rm -rf "$work_dir"
  exit "$status"
}
trap cleanup EXIT

az rest \
  --method get \
  --url "https://graph.microsoft.com/v1.0/applications?\$filter=appId%20eq%20'$ENTRA_AUTH_CLIENT_ID'&\$select=id,passwordCredentials" \
  --output json > "$work_dir/application.json"

application_object_id="$(
  python - "$work_dir/application.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    matches = json.load(stream).get("value", [])
if len(matches) != 1:
    raise SystemExit("Expected exactly one login application.")
print(matches[0]["id"])
PY
)"

az rest \
  --method get \
  --url "https://management.azure.com${KEY_VAULT_RESOURCE_ID}/secrets?api-version=2023-07-01" \
  --output json > "$work_dir/vault-secrets.json"

if python - \
  "$work_dir/application.json" \
  "$work_dir/vault-secrets.json" \
  "$credential_name" \
  "$client_id_secret_name" \
  "$client_secret_secret_name" \
  "$cookie_secret_name" <<'PY'
from datetime import UTC, datetime
import json
import sys

application_path, secrets_path, credential_name, *required_names = sys.argv[1:]
with open(application_path, encoding="utf-8") as stream:
    application = json.load(stream)["value"][0]
with open(secrets_path, encoding="utf-8") as stream:
    secret_names = {
        item["name"].rsplit("/", 1)[-1]
        for item in json.load(stream).get("value", [])
    }
now = datetime.now(UTC)
active_credentials = [
    credential
    for credential in application.get("passwordCredentials", [])
    if credential.get("displayName") == credential_name
    and datetime.fromisoformat(credential["endDateTime"].replace("Z", "+00:00")) > now
]
raise SystemExit(0 if set(required_names) <= secret_names and active_credentials else 1)
PY
then
  echo "OAuth client and cookie secrets are already configured in Key Vault."
  exit 0
fi

python - "$credential_name" > "$work_dir/add-password.json" <<'PY'
from datetime import UTC, datetime, timedelta
import json
import sys

print(json.dumps({
    "passwordCredential": {
        "displayName": sys.argv[1],
        "endDateTime": (datetime.now(UTC) + timedelta(days=180)).isoformat().replace("+00:00", "Z"),
    }
}))
PY

az rest \
  --method post \
  --url "https://graph.microsoft.com/v1.0/applications/$application_object_id/addPassword" \
  --headers Content-Type=application/json \
  --body "@$work_dir/add-password.json" \
  --output json > "$work_dir/new-credential.json"

readarray -t new_credential < <(
  python - "$work_dir/new-credential.json" "$work_dir/client-secret" <<'PY'
import json
import os
import sys

response_path, secret_path = sys.argv[1:]
with open(response_path, encoding="utf-8") as stream:
    credential = json.load(stream)
with open(secret_path, "w", encoding="utf-8") as stream:
    stream.write(credential["secretText"])
os.chmod(secret_path, 0o600)
print(credential["keyId"])
PY
)
new_credential_id="${new_credential[0]}"

printf '%s' "$ENTRA_AUTH_CLIENT_ID" > "$work_dir/client-id"
chmod 600 "$work_dir/client-id"
python - "$work_dir/cookie-secret" <<'PY'
import base64
import os
import sys

with open(sys.argv[1], "w", encoding="utf-8") as stream:
    stream.write(base64.urlsafe_b64encode(os.urandom(32)).decode())
os.chmod(sys.argv[1], 0o600)
PY

put_secret() {
  local name="$1"
  local value_path="$2"
  python - "$value_path" "$new_credential_id" > "$work_dir/secret-request.json" <<'PY'
import json
import sys

value_path, credential_id = sys.argv[1:]
with open(value_path, encoding="utf-8") as stream:
    value = stream.read()
print(json.dumps({
    "properties": {
        "value": value,
        "contentType": "text/plain",
        "attributes": {"enabled": True},
    },
    "tags": {
        "managedBy": "configure-auth-secrets",
        "credentialKeyId": credential_id,
    },
}))
PY
  az rest \
    --method put \
    --url "https://management.azure.com${KEY_VAULT_RESOURCE_ID}/secrets/$name?api-version=2023-07-01" \
    --headers Content-Type=application/json \
    --body "@$work_dir/secret-request.json" \
    --output none
}

put_secret "$client_id_secret_name" "$work_dir/client-id"
put_secret "$client_secret_secret_name" "$work_dir/client-secret"
put_secret "$cookie_secret_name" "$work_dir/cookie-secret"
credential_stored=true

python - "$work_dir/application.json" "$credential_name" "$new_credential_id" > "$work_dir/old-credential-ids" <<'PY'
import json
import sys

application_path, display_name, current_id = sys.argv[1:]
with open(application_path, encoding="utf-8") as stream:
    application = json.load(stream)["value"][0]
for credential in application.get("passwordCredentials", []):
    if credential.get("displayName") == display_name and credential.get("keyId") != current_id:
        print(credential["keyId"])
PY

while IFS= read -r old_credential_id; do
  [ -z "$old_credential_id" ] || remove_password "$old_credential_id"
done < "$work_dir/old-credential-ids"

echo "OAuth client and cookie secrets were stored in Key Vault."
