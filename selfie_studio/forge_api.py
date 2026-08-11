from __future__ import annotations
import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

class ForgeApiError(RuntimeError):
    pass

@dataclass(slots=True)
class ForgeApi:
    base_url: str
    timeout: int = 20

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _request(self, path: str, method: str = "GET", payload: dict | None = None,
                 timeout: int | None = None) -> Any:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as res:
                raw = res.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise ForgeApiError(str(exc)) from exc

    def ping(self) -> dict[str, Any]:
        data = self._request("/sdapi/v1/options")
        return data if isinstance(data, dict) else {}

    def list_models(self) -> list[dict[str, Any]]:
        data = self._request("/sdapi/v1/sd-models")
        return data if isinstance(data, list) else []

    def list_samplers(self) -> list[str]:
        data = self._request("/sdapi/v1/samplers")
        if not isinstance(data, list):
            return []
        return [x.get("name", "") for x in data if isinstance(x, dict) and x.get("name")]

    def script_info(self) -> list[dict[str, Any]]:
        """Return script info (the /sdapi/v1/script-info response).

        Used by clients to discover available always-on scripts and their
        argument schema so callers can populate `alwayson_scripts` safely.
        """
        data = self._request("/sdapi/v1/script-info")
        return data if isinstance(data, list) else []

    def refresh_checkpoints(self) -> None:
        self._request("/sdapi/v1/refresh-checkpoints", method="POST", payload={})

    def set_model(self, checkpoint_title: str) -> None:
        self._request(
            "/sdapi/v1/options",
            method="POST",
            payload={"sd_model_checkpoint": checkpoint_title},
            timeout=180,
        )

    def txt2img(self, payload: dict[str, Any]) -> tuple[list[bytes], dict[str, Any]]:
        data = self._request(
            "/sdapi/v1/txt2img",
            method="POST",
            payload=payload,
            timeout=900,
        )
        if not isinstance(data, dict):
            raise ForgeApiError("Forgeから不正な応答が返りました。")
        images = []
        for encoded in data.get("images", []):
            raw = encoded.split(",", 1)[-1]
            images.append(base64.b64decode(raw))
        return images, data
