extension microsoftGraphV1

@minLength(1)
@description('Display name for the confidential login application')
param displayName string

@minLength(1)
@description('Stable unique name for the confidential login application')
param uniqueName string

@minLength(1)
@description('Exact OAuth2 Proxy callback URI')
param redirectUri string

@description('Object ID of the deployment service principal that owns the application')
param ownerPrincipalId string

@description('Object ID of the Entra group allowed to sign in')
param allowedGroupId string

var defaultAppRoleId = '00000000-0000-0000-0000-000000000000'

resource application 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: uniqueName
  displayName: displayName
  signInAudience: 'AzureADMyOrg'
  web: {
    redirectUris: [redirectUri]
    implicitGrantSettings: {
      enableAccessTokenIssuance: false
      enableIdTokenIssuance: false
    }
  }
  owners: !empty(ownerPrincipalId) ? {
    relationships: [
      ownerPrincipalId
    ]
    relationshipSemantics: 'append'
  } : null
}

resource servicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: application.appId
  appRoleAssignmentRequired: true
}

resource operatorAssignment 'Microsoft.Graph/appRoleAssignedTo@v1.0' = if (!empty(allowedGroupId)) {
  appRoleId: defaultAppRoleId
  principalId: allowedGroupId
  resourceDisplayName: displayName
  resourceId: servicePrincipal.id
}

output clientId string = application.appId
output applicationObjectId string = application.id
output servicePrincipalObjectId string = servicePrincipal.id
