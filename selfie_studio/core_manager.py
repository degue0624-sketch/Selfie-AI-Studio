from __future__ import annotations
from .core import StudioRepository
from .persistence import get_data_dir

_repo: StudioRepository | None = None

def get_repository() -> StudioRepository:
    global _repo
    if _repo is None:
        _repo = StudioRepository(get_data_dir())
        _repo.initialize()
    return _repo
