#!/usr/bin/env python3
"""Publish recent, full-history, and monthly-growth OpenRouter charts."""

from __future__ import annotations

import calendar
import html
import json
import math
import os
import re
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
import matplotlib.ticker as mticker
import requests


API_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
RAMP_INDEX_URL = "https://ramp.com/data/ai-index"
RAMP_CHART_DATA_URL = f"{RAMP_INDEX_URL}/chart-data"
DATASET_START = date(2025, 1, 1)
TRILLION = 1_000_000_000_000
REPORT_DAYS = 90
OUTPUT_DIRECTORY = Path(__file__).resolve().with_name("docs")
RECENT_CHART_PATH = OUTPUT_DIRECTORY / "daily_usage.png"
LONG_TERM_CHART_PATH = OUTPUT_DIRECTORY / "long_term_usage.png"
MONTHLY_GROWTH_CHART_PATH = OUTPUT_DIRECTORY / "monthly_growth.png"
RAMP_WEEKLY_SPEND_CHART_PATH = OUTPUT_DIRECTORY / "ramp_weekly_token_spend.png"
RAMP_SPEND_SHARE_CHART_PATH = OUTPUT_DIRECTORY / "ramp_weekly_spend_share.png"
RAMP_TOKEN_PRICE_CHART_PATH = OUTPUT_DIRECTORY / "ramp_token_prices.png"
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
        rows: list[dict[str, Any]] = []
        response_meta: list[dict[str, str]] = []
        window_start = DATASET_START
        while window_start <= latest:
            # Keep each inclusive request safely below the API's 366-day cap.
            window_end = min(window_start + timedelta(days=364), latest)
            payload = request_json(
                session,
                api_key,
                {
                    "start_date": window_start.isoformat(),
                    "end_date": window_end.isoformat(),
                },
            )
            window_rows, window_meta = validate_response(
                payload, window_start, window_end
            )
            rows.extend(window_rows)
            response_meta.append(window_meta)
            window_start = window_end + timedelta(days=1)

    totals: dict[date, int] = {}
    for row in rows:
        day = row["date"]
        totals[day] = totals.get(day, 0) + int(row["tokens"])
    if latest not in totals:
        raise ReportError(f"The latest date, {latest}, has no token total.")

    calendar_dates = [
        DATASET_START + timedelta(days=i)
        for i in range((latest - DATASET_START).days + 1)
    ]
    values = [float(totals[day]) if day in totals else math.nan for day in calendar_dates]
    meta = {
        "as_of": max(item["as_of"] for item in response_meta),
        "start_date": DATASET_START.isoformat(),
        "end_date": latest.isoformat(),
        "version": ", ".join(sorted({item["version"] for item in response_meta})),
    }
    return calendar_dates, values, meta


def request_ramp_response(
    session: requests.Session,
    url: str,
    params: dict[str, str] | None = None,
    accept: str = "application/json",
    max_attempts: int = 5,
) -> requests.Response:
    headers = {
        "Accept": accept,
        "Referer": RAMP_INDEX_URL,
        "User-Agent": "OpenRouter-Daily-Public-Chart/1.0",
    }
    for attempt in range(1, max_attempts + 1):
        try:
            response = session.get(
                url, headers=headers, params=params, timeout=(10, 90)
            )
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise ReportError(f"Ramp request failed after retries: {exc}") from exc
            time.sleep(min(2 ** (attempt - 1), 30))
            continue

        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            if attempt == max_attempts:
                raise ReportError(
                    f"Ramp returned HTTP {response.status_code} after retries."
                )
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(max(float(retry_after), 1.0), 60.0)
            except ValueError:
                delay = float(min(2 ** (attempt - 1), 30))
            time.sleep(delay)
            continue
        if not response.ok:
            raise ReportError(f"Ramp returned HTTP {response.status_code}.")
        return response
    raise AssertionError("Unreachable retry state")


def fetch_ramp_tokenomics(
) -> tuple[dict[date, dict[str, float]], dict[date, dict[str, tuple[float, float]]]]:
    with requests.Session() as session:
        page = request_ramp_response(
            session,
            RAMP_INDEX_URL,
            accept="text/html,application/xhtml+xml",
        ).text
        version_match = re.search(
            r'\\"dataVersion\\":\\"([A-Za-z0-9_-]+)\\"', page
        ) or re.search(r'"dataVersion":"([A-Za-z0-9_-]+)"', page)
        if version_match is None:
            raise ReportError("Ramp AI Index did not expose a chart data version.")
        version = version_match.group(1)

        payloads: dict[str, Any] = {}
        for dataset in ("tokenVolumes", "tokenPrices"):
            response = request_ramp_response(
                session,
                f"{RAMP_CHART_DATA_URL}/{dataset}",
                {"version": version},
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ReportError(f"Ramp {dataset} returned invalid JSON.") from exc
            if not isinstance(payload, list):
                raise ReportError(f"Ramp {dataset} returned an unexpected response.")
            payloads[dataset] = payload

    weekly_spend: dict[date, dict[str, float]] = {}
    seen_spend: set[tuple[date, str]] = set()
    for number, row in enumerate(payloads["tokenVolumes"], start=1):
        if not isinstance(row, dict) or row.get("metric_key") != "token_spend":
            continue
        try:
            week_start = date.fromisoformat(str(row["period_start"]))
            week_end = date.fromisoformat(str(row["period_end"]))
            series_key = str(row["series_key"]).strip()
            maker = str(row["model_maker"]).strip().lower()
            value = float(row["metric_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReportError(f"Ramp token spend row {number} is invalid.") from exc
        if (
            row.get("period_grain") != "weekly"
            or not series_key
            or not maker
            or not math.isfinite(value)
            or value < 0
            or week_start > week_end
        ):
            raise ReportError(f"Ramp token spend row {number} failed validation.")
        key = (week_end, series_key)
        if key in seen_spend:
            raise ReportError(f"Duplicate Ramp token spend row for {week_end} and {series_key}.")
        seen_spend.add(key)
        maker_values = weekly_spend.setdefault(week_end, {})
        maker_values[maker] = maker_values.get(maker, 0.0) + value

    price_history: dict[date, dict[str, tuple[float, float]]] = {}
    seen_prices: set[tuple[date, str]] = set()
    supported_makers = {"openai", "anthropic"}
    for number, row in enumerate(payloads["tokenPrices"], start=1):
        if not isinstance(row, dict) or row.get("token_type") != "all_tokens":
            continue
        try:
            usage_date = date.fromisoformat(str(row["usage_date"]))
            maker = str(row["model_maker"]).strip().lower()
            cost = float(row["token_cost_usd_7d"])
            count = float(row["token_count_7d"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReportError(f"Ramp token price row {number} is invalid.") from exc
        if maker not in supported_makers:
            continue
        if (
            not math.isfinite(cost)
            or not math.isfinite(count)
            or cost < 0
            or count <= 0
        ):
            raise ReportError(f"Ramp token price row {number} failed validation.")
        key = (usage_date, maker)
        if key in seen_prices:
            raise ReportError(f"Duplicate Ramp token price row for {usage_date} and {maker}.")
        seen_prices.add(key)
        price_history.setdefault(usage_date, {})[maker] = (cost, count)

    if len(weekly_spend) < 12:
        raise ReportError("Ramp returned too little weekly token spend history.")
    if len(price_history) < 30:
        raise ReportError("Ramp returned too little token price history.")
    return weekly_spend, price_history


def ramp_maker_label(maker: str) -> str:
    labels = {
        "anthropic": "Anthropic",
        "cursor": "Cursor",
        "google": "Google",
        "moonshot": "Moonshot",
        "openai": "OpenAI",
        "xai": "xAI",
        "zhipu": "Z.ai",
    }
    return labels.get(maker, maker.replace("_", " ").title())


def rolling_average(
    values: list[float], window: int = 7, min_periods: int = 3
) -> list[float]:
    averages: list[float] = []
    for index in range(len(values)):
        sample = [
            value
            for value in values[max(0, index - window + 1) : index + 1]
            if not math.isnan(value)
        ]
        averages.append(
            sum(sample) / len(sample) if len(sample) >= min_periods else math.nan
        )
    return averages


def validate_chart(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 10_000:
        raise ReportError(f"The chart image {path.name} was not generated correctly.")


def render_recent_chart(
    dates: list[date], token_values: list[float], meta: dict[str, str]
) -> None:
    recent_dates = dates[-REPORT_DAYS:]
    recent_tokens = token_values[-REPORT_DAYS:]
    values = [
        value / TRILLION if not math.isnan(value) else math.nan
        for value in recent_tokens
    ]
    averages = [
        value / TRILLION if not math.isnan(value) else math.nan
        for value in rolling_average(recent_tokens)
    ]
    latest = date.fromisoformat(meta["end_date"])
    latest_value = values[-1]

    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axis = plt.subplots(figsize=(11.5, 5.6))
    axis.plot(
        recent_dates, values, color="#3274A1", linewidth=1.8, label="Daily tokens"
    )
    axis.fill_between(recent_dates, values, 0, color="#3274A1", alpha=0.12)
    axis.plot(
        recent_dates,
        averages,
        color="#E1812C",
        linewidth=2.4,
        label="7-day average",
    )
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
        f"Source: OpenRouter (openrouter.ai/rankings), as of {meta['as_of']}. Public traffic only; missing dates are shown as gaps.",
        fontsize=8.5,
        color="#666666",
    )
    figure.tight_layout(rect=(0, 0.045, 1, 1))
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        RECENT_CHART_PATH, dpi=160, bbox_inches="tight", facecolor="white"
    )
    plt.close(figure)
    validate_chart(RECENT_CHART_PATH)


def render_long_term_chart(
    dates: list[date], token_values: list[float], meta: dict[str, str]
) -> None:
    values = [
        value / TRILLION if not math.isnan(value) else math.nan
        for value in token_values
    ]
    averages = [
        value / TRILLION if not math.isnan(value) else math.nan
        for value in rolling_average(token_values, window=30, min_periods=15)
    ]
    latest = date.fromisoformat(meta["end_date"])

    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axis = plt.subplots(figsize=(11.5, 5.6))
    axis.plot(
        dates,
        values,
        color="#76A5BF",
        linewidth=0.9,
        alpha=0.65,
        label="Daily tokens",
    )
    axis.plot(
        dates,
        averages,
        color="#17365D",
        linewidth=2.5,
        label="30-day average",
    )
    axis.set_title(
        f"OpenRouter Full-History Token Usage — {DATASET_START:%d %b %Y} to {latest:%d %b %Y}",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color="#17365D",
        pad=14,
    )
    axis.set_ylabel("Tokens (trillions)")
    axis.set_xlabel("")
    axis.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axis.tick_params(axis="x", rotation=35)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="upper left", frameon=False, ncols=2)
    axis.margins(x=0.01)
    figure.text(
        0.01,
        0.01,
        f"Source: OpenRouter (openrouter.ai/rankings), as of {meta['as_of']}. Complete available history; missing dates are shown as gaps.",
        fontsize=8.5,
        color="#666666",
    )
    figure.tight_layout(rect=(0, 0.055, 1, 1))
    figure.savefig(
        LONG_TERM_CHART_PATH, dpi=160, bbox_inches="tight", facecolor="white"
    )
    plt.close(figure)
    validate_chart(LONG_TERM_CHART_PATH)


def next_month(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def monthly_growth_series(
    dates: list[date], token_values: list[float], latest: date
) -> tuple[list[date], list[float]]:
    daily = {
        day: value
        for day, value in zip(dates, token_values)
        if not math.isnan(value)
    }
    months: list[date] = []
    totals: list[float | None] = []
    month_start = DATASET_START.replace(day=1)
    while month_start <= latest.replace(day=1):
        days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
        expected = [month_start + timedelta(days=index) for index in range(days_in_month)]
        complete = expected[-1] <= latest and all(day in daily for day in expected)
        months.append(month_start)
        totals.append(sum(daily[day] for day in expected) if complete else None)
        month_start = next_month(month_start)

    growth_months = months[1:]
    growth_values: list[float] = []
    for previous, current in zip(totals, totals[1:]):
        if previous is None or current is None or previous == 0:
            growth_values.append(math.nan)
        else:
            growth_values.append((current / previous - 1) * 100)
    return growth_months, growth_values


def render_monthly_growth_chart(
    dates: list[date], token_values: list[float], meta: dict[str, str]
) -> None:
    latest = date.fromisoformat(meta["end_date"])
    months, growth_values = monthly_growth_series(dates, token_values, latest)
    if not any(not math.isnan(value) for value in growth_values):
        raise ReportError("No complete consecutive months were available for growth rates.")

    positions = list(range(len(months)))
    colors = [
        "#2E8B57" if value >= 0 else "#C75050"
        for value in growth_values
    ]
    figure, axis = plt.subplots(figsize=(11.5, 5.6))
    bars = axis.bar(positions, growth_values, color=colors, width=0.72)
    axis.axhline(0, color="#6B7280", linewidth=1)
    for bar, value in zip(bars, growth_values):
        if math.isnan(value):
            continue
        offset = 3 if value >= 0 else -5
        vertical_alignment = "bottom" if value >= 0 else "top"
        axis.annotate(
            f"{value:+.1f}%",
            xy=(bar.get_x() + bar.get_width() / 2, value),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va=vertical_alignment,
            fontsize=8,
            color="#374151",
        )
    figure.suptitle(
        "OpenRouter Monthly Token Growth — Full History",
        x=0.065,
        y=0.98,
        ha="left",
        fontsize=15,
        fontweight="bold",
        color="#17365D",
    )
    axis.set_ylabel("Month-over-month growth (%)")
    axis.set_xlabel("")
    axis.set_xticks(positions)
    axis.set_xticklabels([month.strftime("%b %y") for month in months], rotation=40, ha="right")
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="x", visible=False)
    axis.margins(x=0.01)
    figure.text(
        0.01,
        0.01,
        f"Source: OpenRouter (openrouter.ai/rankings), as of {meta['as_of']}. Growth requires two adjacent complete months; gaps are blank.",
        fontsize=8.5,
        color="#666666",
    )
    figure.tight_layout(rect=(0, 0.06, 1, 0.94))
    figure.savefig(
        MONTHLY_GROWTH_CHART_PATH,
        dpi=160,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)
    validate_chart(MONTHLY_GROWTH_CHART_PATH)


def render_ramp_weekly_spend_chart(
    weekly_spend: dict[date, dict[str, float]],
) -> None:
    dates = sorted(weekly_spend)
    totals = [sum(weekly_spend[day].values()) for day in dates]
    peak = max(totals)
    if peak <= 0:
        raise ReportError("Ramp weekly token spend contained no positive values.")
    index_values = [value / peak * 100 for value in totals]

    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axis = plt.subplots(figsize=(11.5, 5.6))
    axis.plot(dates, index_values, color="#17365D", linewidth=2.3)
    axis.fill_between(dates, index_values, 0, color="#3274A1", alpha=0.16)
    axis.scatter([dates[-1]], [index_values[-1]], color="#17365D", s=42, zorder=5)
    axis.annotate(
        f"{index_values[-1]:.1f}",
        xy=(dates[-1], index_values[-1]),
        xytext=(-10, 13),
        textcoords="offset points",
        ha="right",
        color="#17365D",
        fontsize=10,
        fontweight="bold",
    )
    axis.set_title(
        "Ramp Aggregate Weekly Token Spend Index",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color="#17365D",
        pad=14,
    )
    axis.set_ylabel("Spend index (peak week = 100)")
    axis.set_xlabel("")
    axis.set_ylim(bottom=0)
    axis.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axis.tick_params(axis="x", rotation=35)
    axis.spines[["top", "right"]].set_visible(False)
    axis.margins(x=0.01)
    figure.text(
        0.01,
        0.01,
        f"Source: Ramp AI Index, through week ending {dates[-1]:%d %b %Y}. Ramp Token Spend Management sample; peak observed aggregate week = 100.",
        fontsize=8.5,
        color="#666666",
    )
    figure.tight_layout(rect=(0, 0.055, 1, 1))
    figure.savefig(
        RAMP_WEEKLY_SPEND_CHART_PATH,
        dpi=160,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)
    validate_chart(RAMP_WEEKLY_SPEND_CHART_PATH)


def render_ramp_spend_share_chart(
    weekly_spend: dict[date, dict[str, float]],
) -> None:
    dates = sorted(weekly_spend)
    cumulative: dict[str, float] = {}
    for maker_values in weekly_spend.values():
        for maker, value in maker_values.items():
            cumulative[maker] = cumulative.get(maker, 0.0) + value
    selected = [
        maker
        for maker, _ in sorted(
            cumulative.items(), key=lambda item: item[1], reverse=True
        )[:5]
    ]
    labels = [ramp_maker_label(maker) for maker in selected] + ["Other"]
    share_series: list[list[float]] = [[] for _ in labels]
    for day in dates:
        maker_values = weekly_spend[day]
        total = sum(maker_values.values())
        if total <= 0:
            raise ReportError(f"Ramp token spend total was zero for {day}.")
        selected_total = 0.0
        for index, maker in enumerate(selected):
            value = maker_values.get(maker, 0.0)
            selected_total += value
            share_series[index].append(value / total * 100)
        share_series[-1].append(max(total - selected_total, 0.0) / total * 100)

    maker_colors = {
        "anthropic": "#E1812C",
        "openai": "#3274A1",
        "google": "#C75050",
        "xai": "#55A868",
        "cursor": "#8172B3",
    }
    palette = [maker_colors.get(maker, "#64B5CD") for maker in selected] + [
        "#9CA3AF"
    ]
    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axis = plt.subplots(figsize=(11.5, 5.6))
    axis.stackplot(
        dates,
        *share_series,
        labels=labels,
        colors=palette,
        alpha=0.9,
    )
    axis.set_title(
        "Ramp Weekly Token Spend Market Share",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color="#17365D",
        pad=14,
    )
    axis.set_ylabel("Share of weekly token spend")
    axis.set_xlabel("")
    axis.set_ylim(0, 100)
    axis.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100, decimals=0))
    axis.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axis.tick_params(axis="x", rotation=35)
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="x", visible=False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False, ncols=3)
    axis.margins(x=0)
    figure.text(
        0.01,
        0.01,
        f"Source: Ramp AI Index, through week ending {dates[-1]:%d %b %Y}. Shares sum to 100%; smaller makers are grouped as Other.",
        fontsize=8.5,
        color="#666666",
    )
    figure.tight_layout(rect=(0, 0.11, 1, 1))
    figure.savefig(
        RAMP_SPEND_SHARE_CHART_PATH,
        dpi=160,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)
    validate_chart(RAMP_SPEND_SHARE_CHART_PATH)


def render_ramp_token_price_chart(
    price_history: dict[date, dict[str, tuple[float, float]]],
) -> dict[str, float | str]:
    maker_values: dict[str, list[float]] = {"openai": [], "anthropic": []}
    dates = [
        day
        for day in sorted(price_history)
        if all(maker in price_history[day] for maker in maker_values)
    ]
    if not dates:
        raise ReportError("Ramp returned no dates shared by OpenAI and Anthropic.")
    combined_values: list[float] = []
    for day in dates:
        records = price_history[day]
        total_cost = 0.0
        total_count = 0.0
        for maker in maker_values:
            record = records.get(maker)
            if record is None:
                maker_values[maker].append(math.nan)
                continue
            cost, count = record
            maker_values[maker].append(cost / count * 1_000_000)
            total_cost += cost
            total_count += count
        combined_values.append(
            total_cost / total_count * 1_000_000 if total_count else math.nan
        )

    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axis = plt.subplots(figsize=(11.5, 5.6))
    axis.plot(
        dates,
        combined_values,
        color="#17365D",
        linewidth=2.7,
        label="OpenAI + Anthropic",
    )
    axis.plot(
        dates,
        maker_values["openai"],
        color="#3274A1",
        linewidth=1.8,
        label="OpenAI",
    )
    axis.plot(
        dates,
        maker_values["anthropic"],
        color="#E1812C",
        linewidth=1.8,
        label="Anthropic",
    )
    latest_combined = combined_values[-1]
    axis.scatter([dates[-1]], [latest_combined], color="#17365D", s=42, zorder=5)
    axis.annotate(
        f"${latest_combined:,.2f}",
        xy=(dates[-1], latest_combined),
        xytext=(-10, 13),
        textcoords="offset points",
        ha="right",
        color="#17365D",
        fontsize=10,
        fontweight="bold",
    )
    axis.set_title(
        "Ramp Historical Blended Token Price — 7-day Rolling",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color="#17365D",
        pad=14,
    )
    axis.set_ylabel("USD per million tokens")
    axis.set_xlabel("")
    axis.set_ylim(bottom=0)
    axis.yaxis.set_major_formatter(mticker.StrMethodFormatter("${x:,.2f}"))
    axis.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axis.tick_params(axis="x", rotation=35)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="upper right", frameon=False, ncols=3)
    axis.margins(x=0.01)
    figure.text(
        0.01,
        0.01,
        f"Source: Ramp AI Index, through {dates[-1]:%d %b %Y}. Realized 7-day blended price across all tokens in the Ramp Token Spend Management sample.",
        fontsize=8.5,
        color="#666666",
    )
    figure.tight_layout(rect=(0, 0.055, 1, 1))
    figure.savefig(
        RAMP_TOKEN_PRICE_CHART_PATH,
        dpi=160,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)
    validate_chart(RAMP_TOKEN_PRICE_CHART_PATH)

    latest_records = price_history[dates[-1]]
    openai_cost, openai_count = latest_records["openai"]
    anthropic_cost, anthropic_count = latest_records["anthropic"]
    return {
        "latest_date": dates[-1].isoformat(),
        "combined_price_per_million": round(latest_combined, 4),
        "openai_price_per_million": round(openai_cost / openai_count * 1_000_000, 4),
        "anthropic_price_per_million": round(
            anthropic_cost / anthropic_count * 1_000_000, 4
        ),
    }


def render_ramp_charts(
    weekly_spend: dict[date, dict[str, float]],
    price_history: dict[date, dict[str, tuple[float, float]]],
) -> dict[str, float | str]:
    render_ramp_weekly_spend_chart(weekly_spend)
    render_ramp_spend_share_chart(weekly_spend)
    meta = render_ramp_token_price_chart(price_history)
    meta["latest_week_end"] = max(weekly_spend).isoformat()
    return meta


def write_page(
    dates: list[date],
    token_values: list[float],
    meta: dict[str, str],
    ramp_meta: dict[str, float | str],
) -> None:
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
        "history_start_date": dates[0].isoformat(),
        "latest_reporting_date": latest.isoformat(),
        "latest_tokens": int(latest_value),
        "latest_tokens_trillions": round(latest_value / TRILLION, 4),
        "daily_change_percent": (
            round(change_percent * 100, 4) if change_percent is not None else None
        ),
        "seven_reported_day_average_trillions": round(seven_day_average / TRILLION, 4),
        "api_as_of": meta["as_of"],
        "ramp_latest_week_end": ramp_meta["latest_week_end"],
        "ramp_token_price_date": ramp_meta["latest_date"],
        "ramp_combined_price_per_million": ramp_meta[
            "combined_price_per_million"
        ],
        "ramp_openai_price_per_million": ramp_meta["openai_price_per_million"],
        "ramp_anthropic_price_per_million": ramp_meta[
            "anthropic_price_per_million"
        ],
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
    .chart-section {{ margin-top:30px; padding-top:24px; border-top:1px solid #e5e7eb; }}
    h2 {{ margin:0 0 5px; color:#17365D; font-size:21px; }}
    .description {{ margin:0 0 12px; color:#5f6368; font-size:13px; }}
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
  <div class="chart-section">
    <h2>Full available history</h2>
    <p class="description">Daily usage and its 30-day trend from 1 January 2025.</p>
    <img src="long_term_usage.png" alt="OpenRouter full-history token usage chart">
  </div>
  <div class="chart-section">
    <h2>Monthly growth</h2>
    <p class="description">Month-over-month growth where both adjacent calendar months are complete.</p>
    <img src="monthly_growth.png" alt="OpenRouter monthly token usage growth chart">
  </div>
  <div class="chart-section">
    <h2>Ramp aggregate weekly token spend</h2>
    <p class="description">Aggregate weekly spend index across models in Ramp's Token Spend Management sample; the peak observed week equals 100.</p>
    <img src="ramp_weekly_token_spend.png" alt="Ramp aggregate weekly token spend index chart">
  </div>
  <div class="chart-section">
    <h2>Ramp weekly token spend market share</h2>
    <p class="description">Weekly model-maker shares shown as a 100% stacked area chart.</p>
    <img src="ramp_weekly_spend_share.png" alt="Ramp weekly token spend market share chart">
  </div>
  <div class="chart-section">
    <h2>Ramp blended token prices</h2>
    <p class="description">Historical seven-day blended price per million tokens for OpenAI, Anthropic, and the two makers combined.</p>
    <img src="ramp_token_prices.png" alt="Ramp historical blended token price chart">
  </div>
  <footer>
    Source: OpenRouter (openrouter.ai/rankings), as of {html.escape(meta['as_of'])}. Private, hidden and zero-data-retention traffic is excluded at source.<br>
    Ramp charts: Ramp AI Index, data from businesses using Ramp Token Spend Management; this sample is not representative of all Ramp AI spending.<br>
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
        weekly_spend, price_history = fetch_ramp_tokenomics()
        render_recent_chart(dates, values, meta)
        render_long_term_chart(dates, values, meta)
        render_monthly_growth_chart(dates, values, meta)
        ramp_meta = render_ramp_charts(weekly_spend, price_history)
        write_page(dates, values, meta, ramp_meta)
        print(f"Published report through {meta['end_date']} in {OUTPUT_DIRECTORY}")
        return 0
    except (ReportError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
