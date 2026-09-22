"""Versioned, append-only master observations. Scores are provisional ordinal labels."""
SCALE_VERSION = "MASTER-SCALE-v0.1-draft"
STAGES = ["unknown", "initial", "shaking", "resting", "ready_for_fixation"]
ACTIONS = ["unknown", "continue_rest", "start_shake", "stop_shake", "start_fixation", "take_sample"]
SCORES = ["aroma_level", "leaf_softness", "red_edge_level", "water_loss_feeling", "confidence"]
LABEL_FIELDS = ["label_id", "revision", "supersedes", "event_id", "batch_id", "host_time_iso",
                "host_monotonic_s", "operator", "scale_version", "stage_label", *SCORES, "next_action", "free_note"]

def validate_label(values):
    if values.get("stage_label") not in STAGES or values.get("next_action") not in ACTIONS:
        raise ValueError("请选择有效阶段和下一步建议")
    for key in SCORES:
        value = values.get(key)
        if value is not None and (type(value) is not int or not 1 <= value <= 5):
            raise ValueError(f"{key} 必须为未判断或1–5分")
    if not isinstance(values.get("free_note", ""), str):
        raise ValueError("备注必须为文本")
