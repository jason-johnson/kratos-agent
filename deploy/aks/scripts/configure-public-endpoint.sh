#!/usr/bin/env bash
set -euo pipefail

: "${AKS_RESOURCE_GROUP:?Set AKS_RESOURCE_GROUP to the approved AKS resource group.}"
: "${AKS_CLUSTER_NAME:?Set AKS_CLUSTER_NAME to the approved AKS cluster name.}"
: "${AZURE_DNS_LABEL:?Set AZURE_DNS_LABEL to the approved regional DNS label.}"
: "${ENDPOINT_OUTPUT_FILE:?Set ENDPOINT_OUTPUT_FILE to a protected file outside the repository.}"

if [[ ! "$AZURE_DNS_LABEL" =~ ^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$ ]]; then
  echo "AZURE_DNS_LABEL must contain 2-63 lowercase letters, numbers, or hyphens." >&2
  exit 2
fi

repository_root="$(git rev-parse --show-toplevel)"
output_path="$(
  python - "$ENDPOINT_OUTPUT_FILE" <<'PY'
import os
import sys

print(os.path.realpath(sys.argv[1]))
PY
)"
case "$output_path" in
  "$repository_root"|"$repository_root"/*)
    echo "ENDPOINT_OUTPUT_FILE must be outside the repository." >&2
    exit 2
    ;;
esac

power_state="$(
  az resource show \
    --resource-group "$AKS_RESOURCE_GROUP" \
    --name "$AKS_CLUSTER_NAME" \
    --resource-type Microsoft.ContainerService/managedClusters \
    --api-version 2024-09-01 \
    --query properties.powerState.code \
    --output tsv
)"
if [ "$power_state" != "Running" ]; then
  echo "AKS must be running before configuring its ingress controller." >&2
  exit 3
fi

kubeconfig="$(mktemp)"
services_file="$(mktemp)"
trap 'rm -f "$kubeconfig" "$services_file"' EXIT
rm -f "$kubeconfig"

az aks get-credentials \
  --resource-group "$AKS_RESOURCE_GROUP" \
  --name "$AKS_CLUSTER_NAME" \
  --file "$kubeconfig" \
  --overwrite-existing \
  --only-show-errors >/dev/null
KUBECONFIG="$kubeconfig" kubelogin convert-kubeconfig -l azurecli

KUBECONFIG="$kubeconfig" kubectl patch nginxingresscontroller default \
  --type merge \
  --patch "$(
    python - "$AZURE_DNS_LABEL" <<'PY'
import json
import sys

print(json.dumps({
    "spec": {
        "loadBalancerAnnotations": {
            "service.beta.kubernetes.io/azure-dns-label-name": sys.argv[1],
        },
    },
}))
PY
  )" >/dev/null

external_ip=""
for _ in $(seq 1 60); do
  KUBECONFIG="$kubeconfig" kubectl get services --all-namespaces -o json > "$services_file"
  external_ip="$(
    python - "$AZURE_DNS_LABEL" "$services_file" <<'PY'
import json
import sys

label = sys.argv[1]
with open(sys.argv[2], encoding="utf-8") as stream:
    services = json.load(stream)["items"]
matches = [
    service
    for service in services
    if service.get("metadata", {}).get("annotations", {}).get(
        "service.beta.kubernetes.io/azure-dns-label-name"
    ) == label
]
if len(matches) != 1:
    raise SystemExit(0)
ingress = matches[0].get("status", {}).get("loadBalancer", {}).get("ingress", [])
if ingress:
    print(ingress[0].get("ip", ""))
PY
  )"
  if [ -n "$external_ip" ]; then
    break
  fi
  sleep 10
done
if [ -z "$external_ip" ]; then
  echo "The Application Routing load balancer did not receive a public IP." >&2
  exit 4
fi

node_resource_group="$(
  az aks show \
    --resource-group "$AKS_RESOURCE_GROUP" \
    --name "$AKS_CLUSTER_NAME" \
    --query nodeResourceGroup \
    --output tsv
)"
fqdn="$(
  az network public-ip list \
    --resource-group "$node_resource_group" \
    --query "[?ipAddress=='$external_ip'].dnsSettings.fqdn | [0]" \
    --output tsv
)"
if [ -z "$fqdn" ]; then
  echo "Azure did not publish a DNS name for the Application Routing public IP." >&2
  exit 5
fi

umask 077
mkdir -p "$(dirname "$output_path")"
printf 'KRATOS_PUBLIC_HOST=%s\n' "$fqdn" > "$output_path"
echo "Azure DNS label configured. The protected hostname was written to ENDPOINT_OUTPUT_FILE."
