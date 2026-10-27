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
  groupMembershipClaims: 'SecurityGroup'
  requiredResourceAccess: [
    {
      resourceAppId: '00000003-0000-0000-c000-000000000000'
      resourceAccess: [
        {
          id: '37f7f235-527c-4136-accd-4a02d197296e'
          type: 'Scope'
        }
        {
          id: '14dad69e-099b-42c9-810b-d002981feec1'
          type: 'Scope'
        }
        {
          id: '64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0'
          type: 'Scope'
        }
      ]
    }
  ]
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
