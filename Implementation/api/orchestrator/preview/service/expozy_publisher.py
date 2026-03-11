import httpx


class ExpozyPublisher:

    def __init__(self, project_url: str, saas_key: str, token: str):
        self.saas_key = saas_key
        self.token = token

    @property
    def _headers(self) -> dict:
        return {
            "authentication": f"basic {self.saas_key}",
            "authorization": f"bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def push_page(self, title: str, html: str, css: str = "") -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://core.expozy.com/api/admin/pages_telegram",
                json={"title": title, "html": html, "css": css or " "},
                headers=self._headers,
            )

        data = resp.json()
        if data.get("status") != 1:
            raise Exception(f"Failed to push page '{title}': {data}")

        return data["obj"]

    async def push_all(self, pages: list[tuple[str, str]]) -> list[dict]:
        results = []
        for title, html in pages:
            page_obj = await self.push_page(title, html)
            results.append(page_obj)
        return results