# OpenRouter Public Token Usage Charts

This repository publishes six static charts: three from OpenRouter's official public rankings
dataset (recent daily usage, full-history usage from 2025-01-01, and monthly growth rates),
plus three from the Ramp AI Index (aggregate weekly token spend, weekly spend market share,
and historical blended token prices).
GitHub Actions refreshes the GitHub Pages site every day at **06:30 Europe/London**, leaving
a 90-minute buffer before a separate Power Automate email runs at 08:00.

Only OpenRouter's already-public aggregate data is published. The API key remains in GitHub
Actions secrets. Email addresses and Microsoft credentials do not belong in this repository.
Ramp charts use the public Ramp AI Index feed and identify Ramp as the source. Ramp tokenomics
represent businesses using Ramp Token Spend Management and are not the entire AI market.

## GitHub setup

1. Create a **public** GitHub repository named `openrouter-daily-chart` without adding a
   README, license, or `.gitignore`.
2. Push this project to its `main` branch.
3. Open **Settings > Secrets and variables > Actions > New repository secret**.
4. Create `OPENROUTER_API_KEY` using a valid OpenRouter API key.
5. Open **Settings > Pages** and set the source to **GitHub Actions**.
6. Open **Actions > Publish OpenRouter Daily Chart > Run workflow** for the initial publish.

The resulting addresses will be:

- Page: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/`
- Recent image: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/daily_usage.png`
- Full-history image: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/long_term_usage.png`
- Monthly-growth image: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/monthly_growth.png`
- Ramp weekly-spend image: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/ramp_weekly_token_spend.png`
- Ramp spend-share image: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/ramp_weekly_spend_share.png`
- Ramp token-price image: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/ramp_token_prices.png`

## Power Automate setup

Create a scheduled cloud flow named `OpenRouter Daily Usage Email`.

### Recurrence

- Frequency: `Day`
- Interval: `1`
- Time zone: `(UTC+00:00) Dublin, Edinburgh, Lisbon, London`
- At these hours: `8`
- At these minutes: `0`

### Office 365 Outlook - Send an email (V2)

Enter the internal recipients directly in Power Automate, separated by semicolons. Do not
store recipient addresses in this public repository.

Suggested subject:

```text
AI Token Usage, Spend and Pricing
```

Use this HTML body after replacing `GITHUB_USERNAME`:

```html
<p>Hello,</p>
<p>Please find below the latest OpenRouter usage and Ramp token-economics charts.</p>

<h3>Recent daily usage</h3>
<p>
  <a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">
    <img
      src="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/daily_usage.png?v=@{formatDateTime(convertTimeZone(utcNow(),'UTC','GMT Standard Time'),'yyyyMMdd')}"
      alt="OpenRouter daily token usage chart"
      style="display:block;width:100%;max-width:1100px;height:auto;border:0;"
    >
  </a>
</p>

<h3>Full available history</h3>
<p>
  <a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">
    <img
      src="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/long_term_usage.png?v=@{formatDateTime(convertTimeZone(utcNow(),'UTC','GMT Standard Time'),'yyyyMMdd')}"
      alt="OpenRouter full-history token usage chart"
      style="display:block;width:100%;max-width:1100px;height:auto;border:0;"
    >
  </a>
</p>

<h3>Monthly growth</h3>
<p>
  <a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">
    <img
      src="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/monthly_growth.png?v=@{formatDateTime(convertTimeZone(utcNow(),'UTC','GMT Standard Time'),'yyyyMMdd')}"
      alt="OpenRouter monthly token usage growth chart"
      style="display:block;width:100%;max-width:1100px;height:auto;border:0;"
    >
  </a>
</p>

<h3>Ramp aggregate weekly token spend</h3>
<p>
  <a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">
    <img
      src="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/ramp_weekly_token_spend.png?v=@{formatDateTime(convertTimeZone(utcNow(),'UTC','GMT Standard Time'),'yyyyMMdd')}"
      alt="Ramp aggregate weekly token spend index"
      style="display:block;width:100%;max-width:1100px;height:auto;border:0;"
    >
  </a>
</p>

<h3>Ramp weekly token spend market share</h3>
<p>
  <a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">
    <img
      src="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/ramp_weekly_spend_share.png?v=@{formatDateTime(convertTimeZone(utcNow(),'UTC','GMT Standard Time'),'yyyyMMdd')}"
      alt="Ramp weekly token spend market share"
      style="display:block;width:100%;max-width:1100px;height:auto;border:0;"
    >
  </a>
</p>

<h3>Ramp historical blended token prices</h3>
<p>
  <a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">
    <img
      src="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/ramp_token_prices.png?v=@{formatDateTime(convertTimeZone(utcNow(),'UTC','GMT Standard Time'),'yyyyMMdd')}"
      alt="Ramp historical blended token prices"
      style="display:block;width:100%;max-width:1100px;height:auto;border:0;"
    >
  </a>
</p>

<p><a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">Open the full report</a></p>
<p style="font-size:12px;color:#666;">Sources: OpenRouter Data API public rankings dataset and Ramp AI Index. Ramp tokenomics cover businesses using Ramp Token Spend Management and are not representative of the entire AI market.</p>
```

Send one test email only to the owner before adding the other recipients. If your tenant blocks
external images by default, the full-report link remains available.

## Local test

```powershell
$env:OPENROUTER_API_KEY = Read-Host "OpenRouter API key"
python generate_chart.py
```

Generated Pages files are placed under `docs/` and excluded from Git because the workflow
publishes them as an ephemeral Pages artifact.
