import re
from datetime import datetime


def clean_prompt(text):
    parts = []
    seen = set()
    for raw in re.split(r"\s*,\s*", str(text or "")):
        item = raw.strip(" ,")
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(item)
    return ", ".join(parts)


def _append_tokens(prompt, tokens):
    base = clean_prompt(prompt)
    existing = {
        x.strip().lower()
        for x in re.split(r"\s*,\s*", base)
        if x.strip()
    }
    items = [x.strip() for x in re.split(r"\s*,\s*", base) if x.strip()]

    for token in tokens:
        token = str(token or "").strip(" ,")
        if token and token.lower() not in existing:
            items.append(token)
            existing.add(token.lower())

    return ", ".join(items)


def _remove_matching(prompt, patterns):
    items = [
        x.strip()
        for x in re.split(r"\s*,\s*", str(prompt or ""))
        if x.strip()
    ]
    result = []
    for item in items:
        low = item.lower()
        if any(re.search(pattern, low) for pattern in patterns):
            continue
        result.append(item)
    return ", ".join(result)


def analyze_instruction(instruction, prompt, negative_prompt):
    """
    Local, deterministic first-stage assistant.
    Returns a proposal dict and never modifies external state.
    """
    instruction = str(instruction or "").strip()
    current_prompt = str(prompt or "").strip()
    current_negative = str(negative_prompt or "").strip()

    if not instruction:
        return {
            "ok": False,
            "summary": "指示が空です。",
            "prompt": current_prompt,
            "negative_prompt": current_negative,
            "actions": [],
        }

    low = instruction.lower()
    proposed_prompt = current_prompt
    proposed_negative = current_negative
    actions = []

    # Prompt cleanup
    if any(key in instruction for key in ("整理", "重複", "きれいに", "整えて")):
        proposed_prompt = clean_prompt(proposed_prompt)
        proposed_negative = clean_prompt(proposed_negative)
        actions.append("Prompt/Negativeの重複・余分な区切りを整理")

    # Background examples
    if "図書館" in instruction:
        proposed_prompt = _remove_matching(
            proposed_prompt,
            (r"\bbackground\b", r"library", r"bedroom", r"hotel", r"cafe")
        )
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["gothic library interior", "bookshelves", "atmospheric interior"]
        )
        actions.append("背景をゴシック図書館へ変更")

    if "寝室" in instruction:
        proposed_prompt = _remove_matching(
            proposed_prompt,
            (r"\bbackground\b", r"library", r"bedroom", r"hotel", r"cafe")
        )
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["elegant bedroom interior", "soft interior lighting"]
        )
        actions.append("背景を寝室へ変更")

    if "夜" in instruction:
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["night", "moonlight", "low-key lighting"]
        )
        actions.append("夜・月明かりの演出を追加")

    # Expression
    if "笑顔" in instruction or "微笑" in instruction:
        proposed_prompt = _remove_matching(
            proposed_prompt,
            (r"frown", r"angry", r"sad", r"cry", r"expression")
        )
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["subtle smile", "gentle expression"]
        )
        actions.append("表情を控えめな笑顔へ変更")

    if "無表情" in instruction:
        proposed_prompt = _remove_matching(
            proposed_prompt,
            (r"smile", r"grin", r"laugh")
        )
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["neutral expression"]
        )
        actions.append("表情を無表情へ変更")

    # Camera
    if any(key in instruction for key in ("引いて", "引き", "遠め", "全身")):
        proposed_prompt = _remove_matching(
            proposed_prompt,
            (r"close-up", r"close up", r"portrait crop", r"bust shot")
        )
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["medium wide shot", "more environment visible"]
        )
        actions.append("カメラを引いた構図へ変更")

    if any(key in instruction for key in ("寄って", "アップ", "接写")):
        proposed_prompt = _remove_matching(
            proposed_prompt,
            (r"wide shot", r"full body", r"medium wide")
        )
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["close-up portrait", "face focus"]
        )
        actions.append("カメラを寄った構図へ変更")

    # Quality
    if "品質" in instruction or "高品質" in instruction:
        proposed_prompt = _append_tokens(
            proposed_prompt,
            ["high quality", "detailed anime illustration", "clean lineart"]
        )
        actions.append("品質タグを追加")

    # Basic negative helpers.
    if any(key in instruction for key in ("崩れ", "手", "指")):
        proposed_negative = _append_tokens(
            proposed_negative,
            ["bad hands", "extra fingers", "missing fingers", "deformed hands"]
        )
        actions.append("手・指崩れのNegativeを追加")

    # If instruction is not covered, produce a no-change proposal rather than pretending.
    if not actions:
        return {
            "ok": False,
            "summary": (
                "この指示はローカル補助では安全に解釈できません。"
                " ChatGPT/API接続後に扱う対象です。"
            ),
            "prompt": current_prompt,
            "negative_prompt": current_negative,
            "actions": [],
        }

    return {
        "ok": True,
        "summary": " / ".join(actions),
        "prompt": clean_prompt(proposed_prompt),
        "negative_prompt": clean_prompt(proposed_negative),
        "actions": actions,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
