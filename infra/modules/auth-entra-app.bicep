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

output clientId string = application.appId
output applicationObjectId string = application.id
