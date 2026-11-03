#!/usr/bin/env python3
"""Publish a static 90-day OpenRouter public-token-usage chart for GitHub Pages."""

from __future__ import annotations

import html
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "MPLCONFIGDIR", str(Path(__file__).resolve().with_name(".matplotlib"))
)

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests


API_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
DATASET_START = date(2025, 1, 1)
TRILLION = 1_000_000_000_000
REPORT_DAYS = 90
OUTPUT_DIRECTORY = Path(__file__).resolve().with_name("docs")
CHART_PATH = OUTPUT_DIRECTORY / "daily_usage.png"
INDEX_PATH = OUTPUT_DIRECTORY / "index.html"
METADATA_PATH = OUTPUT_DIRECTORY / "report.json"


class ReportError(RuntimeError):
    """A safe, user-facing report generation failure."""


def request_json(
    session: requests.Session,
    api_key: str,
    params: dict[str, str] | None = None,
    max_attempts: int = 5,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "OpenRouter-Daily-Public-Chart/1.0",
    }
    for attempt in range(1, max_attempts + 1):
        try:
            response = session.get(
                API_URL, headers=headers, params=params, timeout=(10, 90)
            )
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise ReportError(f"OpenRouter request failed after retries: {exc}") from exc
            time.sleep(min(2 ** (attempt - 1), 30))
            continue

        if response.status_code == 401:
            raise ReportError("OpenRouter rejected OPENROUTER_API_KEY.")
        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            if attempt == max_attempts:
                raise ReportError(
                    f"OpenRouter returned HTTP {response.status_code} after retries."
                )
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(max(float(retry_after), 1.0), 60.0)
            except ValueError:
                delay = float(min(2 ** (attempt - 1), 30))
            time.sleep(delay)
            continue
        if not response.ok:
            raise ReportError(f"OpenRouter returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ReportError("OpenRouter returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ReportError("OpenRouter returned an unexpected response type.")
        return payload
    raise AssertionError("Unreachable retry state")


def validate_response(
    payload: dict[str, Any], expected_start: date | None = None, expected_end: date | None = None
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows = payload.get("data")
    meta = payload.get("meta")
    if not isinstance(rows, list) or not isinstance(meta, dict):
        raise ReportError("The API response is missing data or metadata.")
    required_meta = ("as_of", "start_date", "end_date", "version")
    if any(not isinstance(meta.get(key), str) or not meta[key] for key in required_meta):
        raise ReportError("The API response metadata is incomplete.")
    try:
        meta_start = date.fromisoformat(meta["start_date"])
        meta_end = date.fromisoformat(meta["end_date"])
        datetime.fromisoformat(meta["as_of"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReportError("The API response contains invalid dates.") from exc
    if expected_start is not None and meta_start != expected_start:
        raise ReportError(f"API start date was {meta_start}; expected {expected_start}.")
    if expected_end is not None and meta_end != expected_end:
        raise ReportError(f"API end date was {meta_end}; expected {expected_end}.")

    clean: list[dict[str, Any]] = []
    seen: set[tuple[date, str]] = set()
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ReportError(f"API row {number} is not an object.")
        try:
            day = date.fromisoformat(str(row["date"]))
            model = str(row["model_permaslug"]).strip()
            tokens = int(str(row["total_tokens"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ReportError(f"API row {number} is invalid.") from exc
        if not model or tokens < 0 or not meta_start <= day <= meta_end:
            raise ReportError(f"API row {number} failed validation.")
        key = (day, model)
        if key in seen:
            raise ReportError(f"Duplicate API record for {day} and {model}.")
        seen.add(key)
        clean.append({"date": day, "model": model, "tokens": tokens})
    return clean, {key: str(meta[key]) for key in required_meta}


def fetch_daily_totals(api_key: str) -> tuple[list[date], list[float], dict[str, str]]:
    with requests.Session() as session:
        probe_rows, probe_meta = validate_response(request_json(session, api_key))
        del probe_rows
        latest = date.fromisoformat(probe_meta["end_date"])
        start = max(DATASET_START, latest - timedelta(days=REPORT_DAYS - 1))
        payload = request_json(
            session,
            api_key,
            {"start_date": start.isoformat(), "end_date": latest.isoformat()},
        )
        rows, meta = validate_response(payload, start, latest)

    totals: dict[date, int] = {}
    for row in rows:
        day = row["date"]
        totals[day] = totals.get(day, 0) + int(row["tokens"])
    if latest not in totals:
        raise ReportError(f"The latest date, {latest}, has no token total.")

    calendar_dates = [start + timedelta(days=i) for i in range((latest - start).days + 1)]
    values = [float(totals[day]) if day in totals else math.nan for day in calendar_dates]
    return calendar_dates, values, meta


def rolling_average(values: list[float], window: int = 7) -> list[float]:
    averages: list[float] = []
    for index in range(len(values)):
        sample = [
            value
            for value in values[max(0, index - window + 1) : index + 1]
            if not math.isnan(value)
        ]
        averages.append(sum(sample) / len(sample) if len(sample) >= 3 else math.nan)
    return averages


def render_chart(dates: list[date], token_values: list[float], meta: dict[str, str]) -> None:
    values = [value / TRILLION if not math.isnan(value) else math.nan for value in token_values]
    averages = [
        value / TRILLION if not math.isnan(value) else math.nan
        for value in rolling_average(token_values)
    ]
    latest = date.fromisoformat(meta["end_date"])
    latest_value = values[-1]

    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axis = plt.subplots(figsize=(11.5, 5.6))
    axis.plot(dates, values, color="#3274A1", linewidth=1.8, label="Daily tokens")
    axis.fill_between(dates, values, 0, color="#3274A1", alpha=0.12)
    axis.plot(dates, averages, color="#E1812C", linewidth=2.4, label="7-day average")
    axis.scatter([latest], [latest_value], color="#17365D", s=42, zorder=5)
    axis.annotate(
        f"{latest_value:,.1f}T",
        xy=(latest, latest_value),
        xytext=(-10, 14),
        textcoords="offset points",
        ha="right",
        color="#17365D",
        fontsize=10,
        fontweight="bold",
    )
    axis.set_title(
        f"OpenRouter Daily Public Token Usage — through {latest:%d %b %Y}",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color="#17365D",
        pad=14,
    )
    axis.set_ylabel("Tokens (trillions)")
    axis.set_xlabel("")
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="upper left", frameon=False, ncols=2)
    axis.margins(x=0.01)
    figure.text(
        0.01,
        0.01,
        "Source: OpenRouter Data API. Public traffic only; missing dates are shown as gaps.",
        fontsize=8.5,
        color="#666666",
    )
    figure.tight_layout(rect=(0, 0.045, 1, 1))
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    figure.savefig(CHART_PATH, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    if not CHART_PATH.is_file() or CHART_PATH.stat().st_size < 10_000:
        raise ReportError("The chart image was not generated correctly.")


def write_page(token_values: list[float], meta: dict[str, str]) -> None:
    available = [value for value in token_values if not math.isnan(value)]
    latest = date.fromisoformat(meta["end_date"])
    latest_value = available[-1]
    seven_day_average = sum(available[-7:]) / len(available[-7:])
    change_percent: float | None = None
    if len(available) >= 2 and available[-2]:
        change_percent = latest_value / available[-2] - 1
    change_text = f"{change_percent:+.1%}" if change_percent is not None else "N/A"
    generated_at = datetime.now(timezone.utc)

    report = {
        "latest_reporting_date": latest.isoformat(),
        "latest_tokens": int(latest_value),
        "latest_tokens_trillions": round(latest_value / TRILLION, 4),
        "daily_change_percent": (
            round(change_percent * 100, 4) if change_percent is not None else None
        ),
        "seven_reported_day_average_trillions": round(seven_day_average / TRILLION, 4),
        "api_as_of": meta["as_of"],
        "generated_at_utc": generated_at.isoformat(timespec="seconds"),
    }
    METADATA_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OpenRouter Daily Public Token Usage</title>
  <style>
    body {{ margin:0; background:#f4f6f8; font-family:Segoe UI,Arial,sans-serif; color:#202124; }}
    main {{ max-width:1180px; margin:32px auto; padding:0 20px; }}
    .card {{ background:#fff; border:1px solid #dfe3e8; border-radius:10px; padding:26px; box-shadow:0 2px 8px rgba(23,54,93,.07); }}
    h1 {{ margin:0 0 5px; color:#17365D; font-size:27px; }}
    .date {{ color:#5f6368; margin-bottom:22px; }}
    .stats {{ display:flex; gap:14px; margin-bottom:18px; flex-wrap:wrap; }}
    .stat {{ background:#eef4fb; border-radius:7px; padding:13px 17px; min-width:190px; }}
    .label {{ color:#5f6368; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
    .value {{ color:#17365D; font-size:24px; font-weight:700; margin-top:3px; }}
    img {{ display:block; width:100%; height:auto; }}
    footer {{ color:#5f6368; font-size:12px; margin-top:16px; line-height:1.5; }}
  </style>
</head>
<body><main><section class="card">
  <h1>OpenRouter Daily Public Token Usage</h1>
  <div class="date">Latest completed reporting day: {latest:%A, %d %B %Y}</div>
  <div class="stats">
    <div class="stat"><div class="label">Daily tokens</div><div class="value">{latest_value / TRILLION:,.2f} T</div></div>
    <div class="stat"><div class="label">Daily change</div><div class="value">{html.escape(change_text)}</div></div>
    <div class="stat"><div class="label">7-day average</div><div class="value">{seven_day_average / TRILLION:,.2f} T</div></div>
  </div>
  <img src="daily_usage.png" alt="OpenRouter daily token usage chart">
  <footer>
    Source: OpenRouter Data API public rankings dataset. Private, hidden and zero-data-retention traffic is excluded at source.<br>
    Page generated {generated_at:%Y-%m-%d %H:%M UTC}.
  </footer>
</section></main></body>
</html>"""
    INDEX_PATH.write_text(page, encoding="utf-8")


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set.")
        return 1
    try:
        dates, values, meta = fetch_daily_totals(api_key)
        render_chart(dates, values, meta)
        write_page(values, meta)
        print(f"Published report through {meta['end_date']} in {OUTPUT_DIRECTORY}")
        return 0
    except (ReportError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
