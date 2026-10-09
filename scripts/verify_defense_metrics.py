"""Check frozen metrics against the authoritative processed XLSX and arithmetic.

The original v1 JSON remains supported as a historical document-only freeze.
Workbook checks do not certify hardware or replace raw time-series validation.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def D(value):
    return Decimal(str(value))


def check_metrics(data):
    if data["status"] not in {"reported_in_submission_not_independently_recomputed", "processed_workbook_results_checked"}:
        raise ValueError("unsupported evidence status")
    metrics = data["metrics"]
    ids = {m["id"]: m for m in metrics}
    if len(ids) != len(metrics):
        raise ValueError("duplicate metric id")
    for m in metrics:
        if m.get("measured_source") not in {"reported", "processed_workbook"}:
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
    summary = {"checked_metrics": len(metrics), "derived_formulas_checked": sum(bool(m.get("derivation")) for m in metrics),
               "physical_validation": False, "overall_status": data["status"]}
    if data['status'] == 'processed_workbook_results_checked':
        from workbook_metrics import extract_metrics
        root = Path(__file__).resolve().parents[1]
        path = (root / data['authoritative_workbook']['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('workbook path escapes repository')
        actual = extract_metrics(path)
        if actual['sha256'] != data['authoritative_workbook']['sha256']:
            raise ValueError('authoritative workbook changed; create a new freeze')
        for metric in metrics:
            expected = actual['values'][metric['id']]
            for field, value in expected.items():
                if str(metric[field]) != str(value) and D(metric[field]) != D(value):
                    raise ValueError(f"workbook mismatch for {metric['id']}.{field}")
            if metric.get('workbook_reference') != actual['references'][metric['id']]:
                raise ValueError('workbook reference mismatch')
        if actual['submission_flags'] != data['authoritative_workbook']['submission_flags']:
            raise ValueError('submission flags changed')
        if actual['packet_count_discrepancies'] != data['workbook_notes']['packet_count_discrepancies']:
            raise ValueError('packet-count discrepancy record changed')
        summary.update(workbook_sha256_checked=True, workbook_metrics_checked=len(metrics),
                       cached_calculations_checked=actual['cached_calculations_checked'],
                       submission_flags=actual['submission_flags'],
                       packet_count_discrepancies=actual['packet_count_discrepancies'])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metrics", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs/defense_freeze/metrics_20261009_v2.json")
    args = parser.parse_args()
    summary = check_metrics(json.loads(args.metrics.read_text(encoding="utf-8")))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
