"""Auditable, non-actuating decision preview. There is intentionally no device API.

Approval records only a human review; it NEVER dispatches a motor/air command.
"""
from __future__ import annotations

import argparse
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'qy-shadow-v0.1'


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(text):
    try:
        d = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None


@dataclass(frozen=True)
class ExpertLimits:
    expert_approved: bool = False  # no arbitrary default operating recipe
    supported_tea: str = ''
    min_rest_s: float = 900
    min_rpm: float = 5
    max_rpm: float = 30
    max_duration_s: float = 300

    def __post_init__(self):
        if not (0 < self.min_rest_s and 0 < self.min_rpm <= self.max_rpm and 0 < self.max_duration_s):
            raise ValueError('invalid expert limits')


def suggest(observation, limits=ExpertLimits(), *, now=None):
    """Always shadow-only; fail closed for absent evidence, never claim execution."""
    now = now or utc_now()
    reasons = []
    time = parse_time(observation.get('observed_at_iso'))
    if not time or not 0 <= (now - time).total_seconds() <= 5:
        reasons.append('stale_or_invalid_time')
    if observation.get('source_mode') != 'physical':
        reasons.append('unverified_or_simulated_source')
    if observation.get('quality_flag') != 'OK':
        reasons.append('quality_not_ok')
    if not observation.get('model_validated'):
        reasons.append('model_not_validated')
    if not observation.get('calibration_verified'):
        reasons.append('calibration_not_verified')
    if not limits.expert_approved or observation.get('tea_type') != limits.supported_tea:
        reasons.append('expert_recipe_unapproved_or_mismatch')
    confidence = observation.get('confidence')
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or confidence < 0.8 or confidence > 1:
        reasons.append('low_or_invalid_confidence')
    if observation.get('motor_running') is not False or observation.get('motor_fault') is not False:
        reasons.append('motor_state_not_safe')
    if observation.get('hardware_interlock_verified') is not True:
        reasons.append('interlock_not_verified')
    rest = observation.get('last_shake_elapsed_s')
    if isinstance(rest, bool) or not isinstance(rest, (int, float)) or not math.isfinite(rest) or rest < limits.min_rest_s:
        reasons.append('minimum_rest_not_verified')
    stage = observation.get('stage')
    if stage not in ('resting', 'ready_for_shake'):
        reasons.append('stage_unknown_or_not_ready')
    # This is a recommendation envelope, NOT a dispatchable device command.
    action = 'REVIEW_REQUIRED' if reasons else ('REVIEW_SHAKE_CANDIDATE' if stage == 'ready_for_shake' else 'CONTINUE_REST')
    return {'decision_id': uuid.uuid4().hex, 'version': VERSION, 'batch_id': observation.get('batch_id'),
            'generated_at_iso': now.isoformat(), 'mode': 'shadow_only', 'action': action,
            'reasons': reasons, 'confidence': confidence if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence) else None,
            'recommended_envelope': ({'rpm_min': limits.min_rpm, 'rpm_max': limits.max_rpm,
                                      'duration_s_max': limits.max_duration_s} if action == 'REVIEW_SHAKE_CANDIDATE' else None),
            'actuator_command': None, 'dispatched': False}


def append_review(logfile, decision, operator, response, note=''):
    if response not in ('accept_for_review', 'reject', 'defer'):
        raise ValueError('invalid human review outcome')
    if not operator or not operator.strip():
        raise ValueError('operator required')
    if not decision.get('decision_id') or decision.get('mode') != 'shadow_only' or decision.get('dispatched') is not False:
        raise ValueError('not a shadow decision')
    path = Path(logfile).resolve()
    if 'raw' in path.parts:
        raise ValueError('audit log cannot be stored in raw source directory')
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'type': 'human_shadow_review', 'decision_id': decision['decision_id'],
               'batch_id': decision.get('batch_id'), 'operator': operator,
               'response': response, 'note': note, 'time_utc': utc_now().isoformat(),
               'actuator_command': None, 'dispatched': False}
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')
    return payload


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('observation', type=Path, help='single external, validated state JSON; not raw sensor JSON')
    p.add_argument('--expert-limits', type=Path, help='approved recipe JSON; omit to fail closed')
    p.add_argument('--audit-dir', type=Path, default=Path('data/reports/shadow'))
    args = p.parse_args()
    with args.observation.open(encoding='utf-8') as f:
        observation = json.load(f)
    limits = ExpertLimits(**json.loads(args.expert_limits.read_text(encoding='utf-8'))) if args.expert_limits else ExpertLimits()
    decision = suggest(observation, limits)
    out = args.audit_dir.resolve()
    if 'raw' in out.parts:
        raise ValueError('cannot write to raw')
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'{decision["decision_id"]}.json'
    with path.open('x', encoding='utf-8') as f:
        json.dump({'observation': observation, 'limits': vars(limits), 'decision': decision}, f, ensure_ascii=False, indent=2)
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
