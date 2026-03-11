import re
import shutil
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from shared.config import get_settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _rewrite_links_for_preview(html: str, page_map: dict[str, str]) -> str:
    """
    Rewrite CMS routes to relative preview paths.
    Only used for preview bundles — does not affect production CMS routing.

    page_map: route → filename, e.g. {"/bg/about": "about.html", "/": "index.html"}
    """
    # Sort by longest route first to avoid partial replacements
    for route, filename in sorted(page_map.items(), key=lambda x: -len(x[0])):
        html = html.replace(f'href="{route}"', f'href="./{filename}"')
        html = html.replace(f'href="{route}/"', f'href="./{filename}"')

    return html


def _build_page_map(page_names: list[str]) -> dict[str, str]:
    """
    Build a route → filename map for preview link rewriting.
    Uses EXPOZY CMS route conventions from the catalog.
    """
    from api.orchestrator.ai.providers.catalog_loader import get_catalog

    catalog = get_catalog()
    page_map: dict[str, str] = {}

    for page_name in page_names:
        try:
            route = catalog.route(page_name)
            page_map[route] = f"{page_name}.html"
        except KeyError:
            # Fallback: assume /bg/{page_name} route
            page_map[f"/bg/{page_name}"] = f"{page_name}.html"

    # Homepage special cases
    page_map["/"] = "index.html"
    if "homepage" in page_names:
        page_map.setdefault("/bg/homepage", "index.html")

    return page_map


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
        html_content: str | dict[str, str],
        job_id: Optional[UUID] = None,
    ) -> UUID:
        self._ensure_base_path()

        bundle_id = uuid4()
        bundle_path = self._get_bundle_path(bundle_id)

        try:
            bundle_path.mkdir(parents=True, exist_ok=False)

            if isinstance(html_content, dict):
                # Build link map for preview rewriting
                page_map = _build_page_map(list(html_content.keys()))

                for page_name, html in html_content.items():
                    # Rewrite CMS routes to relative preview paths
                    html = _rewrite_links_for_preview(html, page_map)

                    filename = f"{page_name}.html"
                    (bundle_path / filename).write_text(html, encoding="utf-8")

                # Create index.html from homepage
                if "homepage" in html_content:
                    homepage_html = _rewrite_links_for_preview(
                        html_content["homepage"], page_map
                    )
                    (bundle_path / "index.html").write_text(
                        homepage_html, encoding="utf-8"
                    )
            else:
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