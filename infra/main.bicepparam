// Production values of the ForecastPilot environment. This file is the single
// source for model, pricing, proxy trust, CORS and scale: the deploy job and
// a rebuild both read it, the workflow only supplies the image tag and the
// API key (README, "Infrastructure").
using 'main.bicep'

// Set by the caller's environment; read when az compiles this file.
// DEPLOY_SCOPE: app (default, every push), shared or all (manual).
param scope = readEnvironmentVariable('DEPLOY_SCOPE', 'app')
// IMAGE_TAG: the commit SHA of the pushed image. No default: a missing tag
// stops the deployment before anything is sent.
param imageTag = readEnvironmentVariable('IMAGE_TAG')
// ANTHROPIC_API_KEY: from the GitHub secret of the same name. An empty value
// fails the template's minLength check before anything is sent.
param anthropicApiKey = readEnvironmentVariable('ANTHROPIC_API_KEY', '')

param registryName = 'ca06d26caabdacr'
param workspaceName = 'workspace-forecastpilotrgHJyW'
param environmentName = 'forecastpilot-env'
param appName = 'forecastpilot'

param anthropicModel = 'claude-sonnet-5-5'
// USD per million tokens, Claude Sonnet 5.5 base pricing:
// https://platform.claude.com/docs/en/about-claude/pricing
param anthropicPriceInputPerMtok = '2.0'
param anthropicPriceOutputPerMtok = '10.0'
// Container Apps ingress network; its X-Forwarded-For identifies the caller.
param trustedProxyCidrs = '100.64.0.0/10'
// Browser origin of the web UI on GitHub Pages (origin only, no path).
param corsOrigins = 'https://timur-us.github.io'

param maxReplicas = 10
