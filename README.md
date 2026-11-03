# OpenRouter Daily Public Token Usage Chart

This repository publishes a static 90-day chart from OpenRouter's official public rankings
dataset. GitHub Actions refreshes the GitHub Pages site every day at **06:30 Europe/London**,
leaving a 90-minute buffer before a separate Power Automate email runs at 08:00.

Only OpenRouter's already-public aggregate data is published. The API key remains in GitHub
Actions secrets. Email addresses and Microsoft credentials do not belong in this repository.

## GitHub setup

1. Create a **public** GitHub repository named `openrouter-daily-chart` without adding a
   README, license, or `.gitignore`.
2. Push this project to its `main` branch.
3. Open **Settings → Secrets and variables → Actions → New repository secret**.
4. Create `OPENROUTER_API_KEY` using a valid OpenRouter API key.
5. Open **Settings → Pages** and set the source to **GitHub Actions**.
6. Open **Actions → Publish OpenRouter Daily Chart → Run workflow** for the initial publish.

The resulting addresses will be:

- Page: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/`
- Image: `https://GITHUB_USERNAME.github.io/openrouter-daily-chart/daily_usage.png`

## Power Automate setup

Create a scheduled cloud flow named `OpenRouter Daily Usage Email`.

### Recurrence

- Frequency: `Day`
- Interval: `1`
- Time zone: `(UTC+00:00) Dublin, Edinburgh, Lisbon, London`
- At these hours: `8`
- At these minutes: `0`

### Office 365 Outlook — Send an email (V2)

Enter the internal recipients directly in Power Automate, separated by semicolons. Do not
store recipient addresses in this public repository.

Suggested subject:

```text
OpenRouter Daily Token Usage
```

Use this HTML body after replacing `GITHUB_USERNAME`:

```html
<p>Hello,</p>
<p>Please find below the latest OpenRouter daily public token-consumption chart.</p>
<p>
  <a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">
    <img
      src="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/daily_usage.png"
      alt="OpenRouter daily token usage chart"
      style="display:block;width:100%;max-width:1100px;height:auto;border:0;"
    >
  </a>
</p>
<p><a href="https://GITHUB_USERNAME.github.io/openrouter-daily-chart/">Open the full report</a></p>
<p style="font-size:12px;color:#666;">Source: OpenRouter Data API public rankings dataset.</p>
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
