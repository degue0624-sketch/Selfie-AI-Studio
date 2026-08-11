from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from pathlib import Path

from .persistence import LOCAL_CONFIG_PATH

CONFIG_PATH = LOCAL_CONFIG_PATH

DEFAULT_FORGE_ROOT = r"C:\pinokio\api\stable-diffusion-webui-forge.git\app"

@dataclass(slots=True)
class Settings:
    forge_root: str = DEFAULT_FORGE_ROOT
    forge_url: str = "http://127.0.0.1:7860"
    nas_models_dir: str = ""
    ui_theme: str = "Shiori"
    ui_font: str = "Current / Default"

    @property
    def forge_root_path(self) -> Path:
        return Path(self.forge_root)

    @property
    def models_root(self) -> Path:
        return self.forge_root_path / "models"

    @property
    def checkpoints_dir(self) -> Path:
        return self.models_root / "Stable-diffusion"

    @property
    def lora_dir(self) -> Path:
        return self.models_root / "Lora"

    @property
    def vae_dir(self) -> Path:
        return self.models_root / "VAE"

    @property
    def outputs_dir(self) -> Path:
        return self.forge_root_path / "outputs"

    @property
    def txt2img_dir(self) -> Path:
        return self.outputs_dir / "txt2img-images"

    @property
    def img2img_dir(self) -> Path:
        return self.outputs_dir / "img2img-images"

def load_settings() -> Settings:
    if not CONFIG_PATH.exists():
        return Settings()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return Settings(
            forge_root=data.get("forge_root", DEFAULT_FORGE_ROOT),
            forge_url=data.get("forge_url", "http://127.0.0.1:7860"),
            nas_models_dir=data.get("nas_models_dir", ""),
            ui_theme=data.get("ui_theme", "Shiori"),
            ui_font=data.get("ui_font", "Current / Default"),
        )
    except Exception:
        return Settings()

def save_settings(settings: Settings) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
