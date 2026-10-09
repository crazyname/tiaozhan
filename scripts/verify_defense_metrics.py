"""Validate the internally consistent arithmetic of the *reported* PPT metrics.

This is NOT a physical-data validation tool. It does not assert that documents are
independently verified; the public repo does not contain the source test logs.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def D(value):
    return Decimal(str(value))


def check_metrics(data):
    assert data["status"] == "reported_in_submission_not_independently_recomputed"
    metrics = data["metrics"]
    ids = {m["id"]: m for m in metrics}
    if len(ids) != len(metrics):
        raise ValueError("duplicate metric id")
    for m in metrics:
        if m.get("measured_source") != "reported":
            raise ValueError("unverified metric promoted to proven fact: " + m["id"])
        if not m.get("scope"):
            raise ValueError("metric lacks scope: " + m["id"])
        formula = m.get("derivation")
        if not formula:
            continue
        if formula["formula"] == "numerator/denominator*100":
            top = D(ids[formula["numerator"]]["value"])
            bottom = D(ids[formula["denominator"]]["value"])
            if bottom <= 0:
                raise ValueError("invalid denominator")
            x = top / bottom * D(100)
        elif formula["formula"] == "minuend-subtrahend":
            x = D(ids[formula["minuend"]]["value"]) - D(ids[formula["subtrahend"]]["value"])
        else:
            raise ValueError("unsupported calculation")
        places = int(formula["round_decimals"])
        quantum = D(1).scaleb(-places)
        expected = x.quantize(quantum, rounding=ROUND_HALF_UP)
        if D(m["value"]) != expected:
            raise ValueError(f"{m['id']}: reported={m['value']} recomputed={expected}")
    if D(ids["RUN-02"]["value"]) != D(ids["RUN-01"]["value"]) * 60:
        raise ValueError("480-minute denominator inconsistent with 1 Hz")
    if D(ids["TEA-02"]["value"]) != D(ids["TEA-01"]["value"]) * 60:
        raise ValueError("120-minute denominator inconsistent with 1 Hz")
    if data["physical_evidence_index"]["raw_480min"] is not None or data["physical_evidence_index"]["raw_120min"] is not None:
        raise ValueError("raw file pointers must be manually reviewed before inclusion")
    return {"checked_metrics": len(metrics), "derived_formulas_checked": sum(bool(m.get("derivation")) for m in metrics),
            "physical_validation": False, "overall_status": data["status"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metrics", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs/defense_freeze/metrics_20261009.json")
    args = parser.parse_args()
    summary = check_metrics(json.loads(args.metrics.read_text(encoding="utf-8")))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
