// The Container App. Registry and environment are referenced as existing
// resources, so a push only ever writes this one resource.
//
// Both secrets are part of every deployment: a PUT without a secret would
// remove it from the app. The API key comes from the GitHub secret
// ANTHROPIC_API_KEY (secure parameter), the registry password from the
// registry itself (listCredentials). Never change either in the portal: the
// next deployment writes these values again.

param location string
param appName string
param registryName string
param environmentName string
param image string

@secure()
@minLength(1)
param anthropicApiKey string

param anthropicModel string
param anthropicPriceInputPerMtok string
param anthropicPriceOutputPerMtok string
param trustedProxyCidrs string
param corsOrigins string
param cpu string
param memory string
param maxReplicas int

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: registryName
}

resource managedEnv 'Microsoft.App/managedEnvironments@2026-07-01' existing = {
  name: environmentName
}

// Name of the registry password secret as the portal created it
// (login server without dots, a dash, the admin user name). Kept as is so the
// existing secret is updated in place, never replaced.
var registrySecretName = '${registryName}azurecrio-${registryName}'
var apiKeySecretName = 'anthropic-api-key'

resource app 'Microsoft.App/containerApps@2026-07-01' = {
  name: appName
  location: location
  identity: {
    type: 'None'
  }
  properties: {
    environmentId: managedEnv.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 100
      ingress: {
        external: true
        targetPort: 8000
        // 0 is what the platform reports for HTTP ingress (exposedPort is for TCP).
        exposedPort: 0
        transport: 'Auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: '${registryName}${environment().suffixes.acrLoginServer}'
          username: registryName
          passwordSecretRef: registrySecretName
        }
      ]
      secrets: [
        {
          name: apiKeySecretName
          value: anthropicApiKey
        }
        {
          name: registrySecretName
          value: registry.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'forecastpilot'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            {
              name: 'ANTHROPIC_API_KEY'
              secretRef: apiKeySecretName
            }
            {
              name: 'ANTHROPIC_MODEL'
              value: anthropicModel
            }
            {
              name: 'ANTHROPIC_PRICE_INPUT_PER_MTOK'
              value: anthropicPriceInputPerMtok
            }
            {
              name: 'ANTHROPIC_PRICE_OUTPUT_PER_MTOK'
              value: anthropicPriceOutputPerMtok
            }
            {
              name: 'TRUSTED_PROXY_CIDRS'
              value: trustedProxyCidrs
            }
            {
              name: 'CORS_ORIGINS'
              value: corsOrigins
            }
          ]
        }
      ]
      // minReplicas is left at the platform default 0: scale to zero.
      scale: {
        maxReplicas: maxReplicas
        cooldownPeriod: 300
        pollingInterval: 30
      }
    }
  }
}

output latestRevisionName string = app.properties.latestRevisionName
