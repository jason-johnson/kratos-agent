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

## External-Tenant Operator Handoff

When the target Teams tenant is available only through a dedicated Edge profile,
the operator performs the Teams steps. The repository-side validation does not
require access to that profile.

Before switching profiles, regenerate `dist/kratos-teams-app.zip` from the
selected azd environment and record its SHA-256 checksum:

```bash
python3 scripts/package_teams_app.py
sha256sum dist/kratos-teams-app.zip
```

In the target tenant, validate in this order:

1. Upload the ZIP and open the personal tab in Teams web, then Teams desktop.
2. Without OBO sign-in, verify persona selection, streaming tool activity,
   citations, the read-only confirmation decline, and the handover PDF download.
3. Test OBO separately. The frontend authority and OBO app registrations belong
   to the deployment Entra tenant. A user from another tenant must also be able
   to authenticate in that deployment tenant, typically as a B2B guest, and any
   required consent must already exist. Otherwise record OBO as a cross-tenant
   identity prerequisite, not as a Teams package failure.
4. With eligible test users, sign in as User A and invoke the profile tool. Sign
   out, close the conversation, sign in as User B, and invoke it again. User B
   must never receive User A's profile or delegated context.

Record pass/fail separately for Teams web, Teams desktop, baseline tab behavior,
OBO User A, OBO User B, and cross-user isolation. Do not commit screenshots,
traces, the generated ZIP, tenant identifiers, or deployment endpoints.

## Demo Runbook

Microsoft Learn is scoped to the `generic` persona, while the synthetic ticket
tools belong to `it-service-desk`. Keep that boundary visible in the demo rather
than implying that one persona owns both capabilities.

### Act 1: Grounded Microsoft Learn Research

1. Select **Generic Assistant**.
2. Ask:

   > Use Microsoft Learn documentation to explain when an Azure AI Foundry MCP
   > server should use project managed identity. Search the documentation,
   > fetch the best page for full context, and cite only claims supported by
   > the Microsoft Learn pages you used.

3. Show the streamed Microsoft Learn search/fetch activity and open one cited
   source. Do not present Microsoft Learn Toolbox version 6 as promoted; the
   deployed route remains the direct MCP path.

### Act 2: Synthetic Service Desk Workflow

1. Open the **All** persona tab and select **IT Service Desk L1**. This persona
   is intentionally not in the curated selector.
2. Ask:

   > Triage INC-7001 - walk me through what's known and propose a fix.

3. Show the ServiceNow ticket, work-note, caller, and KB lookups; verify the
   recommendation cites `KB-200` and performs no write.
4. Ask:

   > Reset Jamal Carter's MFA on INC-7001 and update the ticket.

5. Show that Kratos drafts the exact write and requests confirmation before any
   mutation. Decline the action for a read-only demo.
6. Ask:

   > Build me the end-of-shift handover pack for tonight's overnight cover.

7. Download the generated PDF from the response and verify its ticket sections.

All names, tickets, KB articles, employee records, and messages in this flow are
synthetic fixtures from the repository mocks.

Use only synthetic demo data. Generated Teams packages are build artifacts and
must not be committed.
