# Microsoft Teams Personal-Tab Demo

Kratos runs in Teams as a personal tab using the existing Static Web App and
Invocations/SSE backend. The package contains no deployment endpoint or tenant
identifier until it is generated locally.

## Prerequisites

- Deploy the `web` service and select the matching azd environment.
- Enable custom app upload in the target Microsoft 365 tenant.
- Choose one stable UUID for the Teams app. Reuse it when updating the app.

Store the app ID in the gitignored azd environment:

```bash
azd env set TEAMS_APP_ID "$(python3 -c 'import uuid; print(uuid.uuid4())')"
```

## Build the Package

From the repository root:

```bash
python3 scripts/package_teams_app.py
```

The script resolves `AZURE_STATIC_WEB_APP_URL` and `TEAMS_APP_ID` from the
selected azd environment, validates them, and writes
`dist/kratos-teams-app.zip`. To target another HTTPS origin without changing
tracked files, pass `--web-url` explicitly or set `KRATOS_WEB_URL`.

## Install and Validate

1. In Teams, open **Apps**, choose **Manage your apps**, then upload a custom app.
2. Select `dist/kratos-teams-app.zip` and add the Kratos personal app.
3. Verify the tab opens in Teams desktop and Teams web.
4. Verify persona selection, a streaming response, citations, and a generated-file download.
5. Sign in to an OBO-enabled persona, complete one delegated action, sign out, and confirm another user cannot inherit that delegated context.

Use only synthetic demo data. Generated Teams packages are build artifacts and
must not be committed.
