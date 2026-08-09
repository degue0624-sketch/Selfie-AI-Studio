import base64
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path


class OpenAIApiError(RuntimeError):
    pass


def _endpoint(base_url):
    return str(base_url or "https://api.openai.com/v1").rstrip("/") + "/responses"


def _extract_output_text(data):
    if isinstance(data, dict):
        direct = data.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        for output in data.get("output") or []:
            if not isinstance(output, dict):
                continue
            for content in output.get("content") or []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return ""


def _request(api_key, base_url, payload, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _endpoint(base_url),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            message = (
                ((parsed.get("error") or {}).get("message"))
                or raw
            )
        except Exception:
            message = raw or str(e)

        if e.code == 400:
            raise OpenAIApiError(
                "OpenAI APIへ送った設定が受け付けられませんでした。\n"
                f"{message}"
            )
        if e.code == 401:
            raise OpenAIApiError(
                "APIキーが無効、または認証に失敗しました。"
            )
        if e.code == 403:
            raise OpenAIApiError(
                "このAPIキーまたはProjectには、この操作の権限がありません。"
            )
        if e.code == 404:
            raise OpenAIApiError(
                "指定したModelまたはAPIエンドポイントが見つかりません。"
            )
        if e.code == 429:
            raise OpenAIApiError(
                "APIの利用上限、残高不足、またはレート制限に達しました。"
            )
        raise OpenAIApiError(f"OpenAI API HTTP {e.code}: {message}")
    except urllib.error.URLError as e:
        raise OpenAIApiError(f"OpenAI APIへ接続できません: {e.reason}")
    except TimeoutError:
        raise OpenAIApiError("OpenAI APIの応答がタイムアウトしました。")


def test_connection(api_key, model, base_url):
    if not api_key:
        raise OpenAIApiError("APIキーが未設定です。")
    if not model:
        raise OpenAIApiError("Modelが未設定です。")

    payload = {
        "model": model,
        "input": "Reply with exactly: OK",
        "store": False,
        "reasoning": {"effort": "none"},
        # Responses API rejects values below its current minimum.
        # Keep enough room for model output/reasoning while still making
        # the connection test very small and inexpensive.
        "max_output_tokens": 64,
    }
    data = _request(api_key, base_url, payload, timeout=30)
    text = _extract_output_text(data)
    return text or "OK"


def analyze_prompt(
    *,
    api_key,
    model,
    base_url,
    instruction,
    prompt,
    negative_prompt,
    character_context="",
    builder_context="",
):
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "prompt": {"type": "string"},
            "negative_prompt": {"type": "string"},
            "changed_areas": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "summary",
            "reason",
            "prompt",
            "negative_prompt",
            "changed_areas",
        ],
        "additionalProperties": False,
    }

    developer_instruction = (
        "You edit Stable Diffusion anime-image prompts for a local image studio. "
        "Return only the requested structured result. "
        "Preserve identity-critical character traits unless the user explicitly "
        "asks to change them. Do not modify Project, model, LoRA, sampler, CFG, "
        "steps, dimensions, or other generation settings. "
        "Apply the user's requested scope narrowly. "
        "Do not invent character-specific visual traits not present in the context. "
        "Prompt and negative_prompt must be practical comma-separated prompt text."
    )

    user_input = (
        f"User instruction:\n{instruction}\n\n"
        f"Character context:\n{character_context or '(none)'}\n\n"
        f"Prompt Builder context:\n{builder_context or '(none)'}\n\n"
        f"Current Prompt:\n{prompt}\n\n"
        f"Current Negative Prompt:\n{negative_prompt}"
    )

    payload = {
        "model": model,
        "instructions": developer_instruction,
        "input": user_input,
        "store": False,
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "studio_prompt_edit",
                "strict": True,
                "schema": schema,
            }
        },
    }

    data = _request(api_key, base_url, payload, timeout=90)
    text = _extract_output_text(data)
    if not text:
        raise OpenAIApiError("API応答から変更案を取得できませんでした。")

    try:
        result = json.loads(text)
    except Exception as e:
        raise OpenAIApiError(
            f"API応答のJSON解析に失敗しました: {e}"
        )

    return {
        "ok": True,
        "summary": str(result.get("summary") or ""),
        "reason": str(result.get("reason") or ""),
        "prompt": str(result.get("prompt") or prompt),
        "negative_prompt": str(
            result.get("negative_prompt") or negative_prompt
        ),
        "actions": [
            str(x)
            for x in (result.get("changed_areas") or [])
        ],
        "source": "OpenAI API",
    }


def _image_data_url(image_path):
    path = Path(image_path)
    if not path.exists():
        raise OpenAIApiError(f"画像ファイルが見つかりません: {path}")

    suffix = path.suffix.lower()
    allowed = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime = allowed.get(suffix)
    if not mime:
        raise OpenAIApiError(
            "AI画像解析で送信できる形式は PNG / JPEG / WEBP / GIF です。"
        )

    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def analyze_image_review(
    *,
    api_key,
    model,
    base_url,
    image_path,
    prompt="",
    negative_prompt="",
    character_context="",
    generation_context="",
):
    schema = {
        "type": "object",
        "properties": {
            "ratings": {
                "type": "object",
                "properties": {
                    "face": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "eyes": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "hair": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "background": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "lighting": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "composition": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "pose": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "hands": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                    "overall": {"type": "string", "enum": ["良い", "普通", "要修正"]},
                },
                "required": [
                    "face", "eyes", "hair", "background", "lighting",
                    "composition", "pose", "hands", "overall"
                ],
                "additionalProperties": False,
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
            },
            "issues": {
                "type": "array",
                "items": {"type": "string"},
            },
            "priorities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "prompt_suggestion": {"type": "string"},
            "negative_prompt_suggestion": {"type": "string"},
            "lora_suggestions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "generation_setting_suggestions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "summary": {"type": "string"},
        },
        "required": [
            "ratings",
            "strengths",
            "issues",
            "priorities",
            "prompt_suggestion",
            "negative_prompt_suggestion",
            "lora_suggestions",
            "generation_setting_suggestions",
            "summary",
        ],
        "additionalProperties": False,
    }

    instructions = (
        "You are an image-quality reviewer for an anime Stable Diffusion workflow. "
        "Evaluate only visible image quality and consistency with the supplied text context. "
        "Do not identify real people. Do not invent hidden character facts. "
        "When character context is insufficient, judge visual consistency only. "
        "Prompt suggestions must preserve identity-critical traits unless clearly wrong in the image. "
        "LoRA and generation-setting suggestions are advisory only and should be empty unless there is "
        "a concrete reason to suggest a change. Keep recommendations concise and actionable."
    )

    context_text = (
        f"Current prompt:\n{prompt or '(none)'}\n\n"
        f"Current negative prompt:\n{negative_prompt or '(none)'}\n\n"
        f"Character context:\n{character_context or '(none)'}\n\n"
        f"Generation context:\n{generation_context or '(none)'}\n\n"
        "Review the attached generated image. Focus on face, eyes, hair, background, lighting, "
        "composition, pose, hands/fingers, and overall quality. "
        "Return practical improvement suggestions."
    )

    payload = {
        "model": model,
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": context_text,
                    },
                    {
                        "type": "input_image",
                        "image_url": _image_data_url(image_path),
                        "detail": "high",
                    },
                ],
            }
        ],
        "store": False,
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "studio_image_review",
                "strict": True,
                "schema": schema,
            }
        },
    }

    data = _request(api_key, base_url, payload, timeout=120)
    text = _extract_output_text(data)
    if not text:
        raise OpenAIApiError("API応答から画像解析結果を取得できませんでした。")

    try:
        result = json.loads(text)
    except Exception as e:
        raise OpenAIApiError(
            f"画像解析結果のJSON解析に失敗しました: {e}"
        )

    result["ok"] = True
    result["source"] = "OpenAI API"
    return result
