#!/usr/bin/env python3
"""Extract the HC GPU rental-price history from its source workbook."""

from __future__ import annotations

import argparse
import csv
import math
from datetime import date, datetime
from pathlib import Path

import openpyxl


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "data" / "gpu_rental_prices.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path, help="GPU Rental Prices .xlsm workbook")
    parser.add_argument("--sheet", default="HC", help="Worksheet to extract (default: HC)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def extract_rows(workbook_path: Path, sheet_name: str) -> list[tuple[date, float, float, float]]:
    workbook = openpyxl.load_workbook(
        workbook_path,
        read_only=True,
        data_only=True,
        keep_vba=workbook_path.suffix.lower() == ".xlsm",
    )
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Worksheet {sheet_name!r} was not found in {workbook_path}.")
        sheet = workbook[sheet_name]
        records: list[tuple[date, float, float, float]] = []
        seen_dates: set[date] = set()
        rows = sheet.iter_rows(
            min_row=3,
            max_row=sheet.max_row,
            min_col=2,
            max_col=5,
            values_only=True,
        )
        for row_number, (raw_day, raw_a100, raw_h100, raw_b200) in enumerate(
            rows, start=3
        ):
            if all(value is None for value in (raw_day, raw_a100, raw_h100, raw_b200)):
                continue
            if isinstance(raw_day, datetime):
                day = raw_day.date()
            elif isinstance(raw_day, date):
                day = raw_day
            else:
                raise ValueError(f"HC row {row_number} has an invalid date: {raw_day!r}.")
            try:
                prices = tuple(float(value) for value in (raw_a100, raw_h100, raw_b200))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"HC row {row_number} has an invalid GPU price.") from exc
            if any(not math.isfinite(value) or value <= 0 for value in prices):
                raise ValueError(f"HC row {row_number} has a non-positive GPU price.")
            if day in seen_dates:
                raise ValueError(f"HC contains a duplicate date: {day}.")
            seen_dates.add(day)
            records.append((day, prices[0], prices[1], prices[2]))
    finally:
        workbook.close()

    records.sort(key=lambda record: record[0])
    if len(records) < 30:
        raise ValueError("HC contains too little valid GPU rental-price history.")
    return records


def write_csv(output_path: Path, records: list[tuple[date, float, float, float]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "date",
                "a100_usd_per_hour",
                "h100_usd_per_hour",
                "b200_usd_per_hour",
            ]
        )
        for day, a100, h100, b200 in records:
            writer.writerow(
                [day.isoformat(), f"{a100:.10g}", f"{h100:.10g}", f"{b200:.10g}"]
            )


def main() -> None:
    args = parse_args()
    records = extract_rows(args.workbook.resolve(), args.sheet)
    output = args.output.resolve()
    write_csv(output, records)
    print(
        f"Wrote {len(records)} GPU rental-price rows from {records[0][0]} "
        f"through {records[-1][0]} to {output}."
    )


if __name__ == "__main__":
    main()
