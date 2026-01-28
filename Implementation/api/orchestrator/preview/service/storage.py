import shutil
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from shared.config import get_settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class StorageService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._base_path = Path(self._settings.previews_path)

    def _ensure_base_path(self) -> None:
        self._base_path.mkdir(parents=True, exist_ok=True)

    def _get_bundle_path(self, bundle_id: UUID) -> Path:
        return self._base_path / str(bundle_id)

    async def create_bundle(
        self,
        template: dict[str, Any],   
        html_content: str,
        job_id: Optional[UUID] = None,  
    ) -> UUID:
        self._ensure_base_path()

        bundle_id = uuid4()
        bundle_path = self._get_bundle_path(bundle_id)

        try:
            bundle_path.mkdir(parents=True, exist_ok=False)
            (bundle_path / "index.html").write_text(html_content, encoding="utf-8")

            logger.info(
                "Bundle created",
                bundle_id=str(bundle_id),
                job_id=str(job_id) if job_id else None,
                path=str(bundle_path),
            )
            return bundle_id

        except Exception as e:
            if bundle_path.exists():
                shutil.rmtree(bundle_path, ignore_errors=True)
            logger.error("Failed to create bundle", error=str(e))
            raise


_storage: Optional[StorageService] = None


def get_storage() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage
