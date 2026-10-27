#!/usr/bin/env bash
set -euo pipefail

output_image="${OUTPUT_IMAGE:-oauth2-proxy:validation}"
source_commit='bea3f04bf87b93f5354f392fe2216a88e6031350'
source_sha256='4b62ea81dce2014981fa62e19cf1c2c03400de1c3e6a8107b57cc6190285e133'
build_image='docker.io/library/golang@sha256:632dff7351799a5a9954a48dc06670ade72ee44e8c351cdc0e5a57af2fea21af'
runtime_image='gcr.io/distroless/static-debian12@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab'
version='v7.15.5-go1.26.10'

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_dir/../../.." && pwd)"
patch_file="$repository_root/deploy/aks/patches/oauth2-proxy-v7.15.5-x-net.patch"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

curl \
  --fail \
  --location \
  --silent \
  --show-error \
  --output "$work_dir/source.tar.gz" \
  "https://github.com/oauth2-proxy/oauth2-proxy/archive/${source_commit}.tar.gz"
printf '%s  %s\n' "$source_sha256" "$work_dir/source.tar.gz" | sha256sum --check --status

mkdir "$work_dir/source"
tar --extract --gzip --file "$work_dir/source.tar.gz" --strip-components=1 --directory "$work_dir/source"
patch --directory "$work_dir/source" --strip=1 --fuzz=0 --input "$patch_file"

docker build \
  --build-arg "BUILD_IMAGE=$build_image" \
  --build-arg "RUNTIME_IMAGE=$runtime_image" \
  --build-arg "VERSION=$version" \
  --tag "$output_image" \
  "$work_dir/source"

actual_version="$(docker run --rm "$output_image" --version)"
expected_version="oauth2-proxy $version (built with go1.26.10)"
if [ "$actual_version" != "$expected_version" ]; then
  echo "Unexpected OAuth2 Proxy build identity." >&2
  exit 1
fi
