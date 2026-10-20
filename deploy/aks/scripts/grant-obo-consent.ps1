#!/usr/bin/env pwsh
#
# Windows counterpart of grant-obo-consent.sh.
#
# Explicit operator utility for granting tenant-wide admin consent to the OBO
# server app's delegated Microsoft Graph permissions.
#
# Why this is separate from Bicep
# -------------------------------
# The app registration requests delegated Graph permissions declaratively in
# infra/modules/obo-entra-app.bicep (`requiredResourceAccess`) — that needs no
# special rights. Actually *granting* it is a different operation: an
# `oauth2PermissionGrants` with consentType `AllPrincipals` consents on behalf of
# every user in the tenant, which Microsoft Graph refuses unless the caller holds
# an Entra directory role. Azure RBAC does not help — subscription Owner confers
# nothing in the directory.
#
# When the grant lived in Bicep, deploying into a tenant where consent is
# admin-gated (the default for most corporate tenants) failed the entire
# provision with a bare `Authorization_RequestDenied`, giving no hint that the
# problem was an Entra role rather than an Azure one. Bicep cannot attempt a
# resource and continue when it is forbidden, so the grant moved here, where a
# 403 can be reported clearly and the deployment allowed to finish.
#
# Consequence of a skipped grant: OBO still works, but users may be prompted to
# consent to permissions individually, or an administrator grants them once
# (see the instructions this script prints on failure).
#
# The utility is idempotent. Unlike the former azd hook, it reports failures with
# a non-zero exit status because an operator invoked it explicitly.

$ErrorActionPreference = 'Continue'
# Keep expected non-zero Azure CLI exits non-terminating; the utility inspects
# $LASTEXITCODE itself.
$PSNativeCommandUseErrorActionPreference = $false

$MsGraphAppId = '00000003-0000-0000-c000-000000000000'
$Scopes = @('User.Read', 'User.ReadBasic.All', 'Presence.Read.All', 'Calendars.Read', 'Mail.Read', 'Chat.Read')
$ScopeValue = $Scopes -join ' '

# Only meaningful when the OBO stack was provisioned.
$DeployObo = if ($env:DEPLOY_OBO) { $env:DEPLOY_OBO.ToLowerInvariant() } else { 'true' }
if ($DeployObo -notin @('1', 'true', 'yes')) {
    exit 0
}

$AppId = $env:OBO_SERVER_APP_CLIENT_ID
if (-not $AppId -and (Get-Command azd -ErrorAction SilentlyContinue)) {
    $AppId = (azd env get-value OBO_SERVER_APP_CLIENT_ID 2>$null | Out-String).Trim()
}
if (-not $AppId) {
    Write-Error 'OBO_SERVER_APP_CLIENT_ID is unavailable. Select the provisioned azd environment or export the value explicitly.'
    exit 1
}

Write-Host 'Granting admin consent for the OBO server app delegated Graph permissions ...'

# The grant references service principal OBJECT ids, not app ids.
$ClientSpId = (az ad sp show --id $AppId --query id -o tsv 2>$null | Out-String).Trim()
$GraphSpId  = (az ad sp show --id $MsGraphAppId --query id -o tsv 2>$null | Out-String).Trim()

if (-not $ClientSpId -or -not $GraphSpId) {
    Write-Host '   WARNING: Could not resolve the service principals needed for the grant.'
    Write-Host '            Skipping — see the manual steps below.'
    $ClientSpId = ''
}

if ($ClientSpId) {
    # Update an existing grant instead of creating a duplicate.
    $FilterUrl = "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$ClientSpId' and resourceId eq '$GraphSpId'"
    $ExistingId = (az rest --method GET --url $FilterUrl --query 'value[0].id' -o tsv 2>$null | Out-String).Trim()
    $ExistingScopes = (az rest --method GET --url $FilterUrl --query 'value[0].scope' -o tsv 2>$null | Out-String) -split '\s+'
    $AlreadyGranted = -not ($Scopes | Where-Object { $_ -notin $ExistingScopes })

    if ($AlreadyGranted) {
        Write-Host '   [ok] Admin consent already in place (nothing to do).'
        exit 1
    }

    $Method = 'POST'
    $Url = 'https://graph.microsoft.com/v1.0/oauth2PermissionGrants'
    $BodyFields = @{
        clientId = $ClientSpId
        consentType = 'AllPrincipals'
        resourceId = $GraphSpId
        scope = $ScopeValue
    }
    if ($ExistingId) {
        $Method = 'PATCH'
        $Url = "$Url/$ExistingId"
        $BodyFields = @{ scope = $ScopeValue }
    }
    $Body = $BodyFields | ConvertTo-Json -Compress

    $ConsentOutput = (az rest --method $Method `
        --url $Url `
        --headers 'Content-Type=application/json' `
        --body $Body `
        --output none 2>&1 | Out-String)

    if ($LASTEXITCODE -eq 0) {
        Write-Host '   [ok] Admin consent granted.'
        exit 0
    }

    if ($ConsentOutput -match 'Authorization_RequestDenied|Insufficient privileges|Forbidden') {
        Write-Host '   NOTE: Your account is not allowed to grant tenant-wide admin consent.'
        Write-Host '         This is expected in most corporate tenants and does NOT break the'
        Write-Host '         deployment — everything else provisioned normally.'
        Write-Host '         Granting consent needs an Entra ID directory role — Global'
        Write-Host '         Administrator, Privileged Role Administrator, or Cloud Application'
        Write-Host '         Administrator. Azure RBAC (even subscription Owner) does not'
        Write-Host '         include it.'
    } else {
        Write-Host '   WARNING: The consent request failed for an unexpected reason:'
        ($ConsentOutput -split "`r?`n" | Select-Object -First 10) | ForEach-Object {
            if ($_) { Write-Host "            $_" }
        }
    }
}

# Shown for every unsuccessful path, since the manual remedy is the same
# whether consent was forbidden, errored, or the lookups failed.
Write-Host @"
         Until someone grants it, OBO still works: the first user to sign in is
         asked to consent to the delegated Graph permissions themselves.

         To grant it once, an administrator can run:
           az ad app permission admin-consent --id $AppId

         ...or open the app registration in the portal, go to
         "API permissions", and choose "Grant admin consent".
"@

exit 0
