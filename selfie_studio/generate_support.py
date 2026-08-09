def clamp_queue_count(value, minimum=1, maximum=20):
    try:
        number = int(value)
    except Exception:
        number = minimum
    return max(minimum, min(maximum, number))


def build_generation_payload(*, prompt, negative_prompt, steps, cfg_scale, width, height, sampler_name):
    return {
        "prompt": prompt or "",
        "negative_prompt": negative_prompt or "",
        "steps": steps,
        "cfg_scale": cfg_scale,
        "width": width,
        "height": height,
        "sampler_name": sampler_name or "",
        "batch_size": 1,
        "n_iter": 1,
    }


def production_check_summary(checks):
    return "制作チェック: " + " / ".join(
        ("✓ " if ok else "× ") + str(label)
        for label, ok in (checks or [])
    )


def split_missing_checks(checks, required):
    required = set(required or ())
    hard_missing = []
    optional_missing = []
    for label, ok in (checks or []):
        if ok:
            continue
        (hard_missing if label in required else optional_missing).append(label)
    return hard_missing, optional_missing


def compact_lora_text(loras):
    if not loras:
        return "なし"
    result = []
    if isinstance(loras, dict):
        for name, weight in loras.items():
            try:
                result.append(f"{name}:{float(weight):g}")
            except Exception:
                result.append(str(name))
    return ", ".join(result) if result else "なし"
