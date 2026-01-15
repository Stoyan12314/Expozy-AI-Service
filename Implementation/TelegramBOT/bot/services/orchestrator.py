import httpx
from bot.config import Config


class OrchestratorService:
    async def generate(self, user_id: str, chat_id: str, prompt: str) -> dict:
        """Send prompt to orchestrator, return preview_url or error"""
        try:
            async with httpx.AsyncClient(timeout=Config.ORCHESTRATOR_TIMEOUT) as client:
                response = await client.post(
                    Config.ORCHESTRATOR_URL,
                    json={
                        "telegram_user_id": user_id,
                        "telegram_chat_id": chat_id,
                        "prompt": prompt,
                    },
                )
                
                if response.status_code == 200:
                    return {"success": True, **response.json()}
                elif response.status_code == 429:
                    return {"success": False, "error": "Rate limit exceeded"}
                else:
                    return {"success": False, "error": response.json().get("error", "Unknown error")}
                    
        except httpx.TimeoutException:
            return {"success": False, "error": "Timeout - still processing"}
        except httpx.ConnectError:
            return {"success": False, "error": "Cannot connect to orchestrator"}
        except Exception as e:
            return {"success": False, "error": str(e)}


orchestrator = OrchestratorService()
