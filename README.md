# ForecastPilot 📈

**Time-series forecasting + AI-generated market commentary, shipped as a production-grade service.**

A FastAPI service that pulls market data, produces a statistical forecast with backtest metrics, and uses Claude (Anthropic API) to generate a bilingual (EN/DE) management commentary — with token/cost tracking and an automated "numbers-match" guard against hallucinated figures.

> ⚠️ Educational portfolio project — **not investment advice**.

## Why this exists

Built to demonstrate end-to-end ML & GenAI engineering: model serving, containerization, CI/CD, cloud deployment (Azure), observability, and LLM output evaluation.

## Architecture (target state)

```
Client → FastAPI (/forecast) → Data layer (yfinance)
                             → Forecasting (statsmodels)
                             → Claude commentary (Anthropic API)

CI/CD:      GitHub Actions (lint → test → build → deploy)
Infra:      Azure Container Apps, provisioned via Bicep (IaC)
Observability: Application Insights + custom token/cost metrics
```

## Quickstart (local)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # add your ANTHROPIC_API_KEY
uvicorn app.main:app --reload
# open http://127.0.0.1:8000/docs
```

Try it: `GET /forecast/SAP.DE?horizon=30&commentary=true&language=de`

## Docker

```bash
docker build -t forecastpilot .
docker run --env-file .env -p 8000:8000 forecastpilot
```

## Roadmap

- [ ] **W1–2** Core service local — API, forecasting, LLM layer, tests, Docker
- [ ] **W3** Azure Container Apps deploy (manual first, then Bicep)
- [ ] **W4** CI/CD — GitHub Actions deploys on push to `main`
- [ ] **W5** Observability — App Insights, token/cost metrics, LLM eval suite (numbers-match guard)
- [ ] **W6** Polish — architecture diagram, screenshots, live demo URL
- [ ] **W7** RAG add-on — commentary grounded in uploaded reports, with citations

## Cost controls

- **Anthropic:** input/output tokens tracked per request; monthly budget limit set in the console
- **Azure:** budget alert in Cost Management; Container Apps scale-to-zero when idle

## License

MIT
