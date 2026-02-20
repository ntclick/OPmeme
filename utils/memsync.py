import logging
import requests
import uuid
from config import MEMSYNC_API_KEY

logger = logging.getLogger(__name__)

class MemSyncClient:
    """
    Client for MemSync API (memsync.ai).
    Provides long-term memory for the Sentinel agent.
    """
    BASE_URL = "https://api.memchat.io/v1"

    def __init__(self):
        self.api_key = MEMSYNC_API_KEY
        if not self.api_key:
            logger.warning("MEMSYNC_API_KEY not set. Memory features disabled.")
    
    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """
        Search for relevant past memories/analyses.
        """
        if not self.is_enabled():
            return []

        try:
            url = f"{self.BASE_URL}/memories/search"
            headers = {
                "X-API-Key": self.api_key,
                "Content-Type": "application/json"
            }
            data = {
                "query": query,
                "limit": limit,
                "rerank": True
            }
            response = requests.post(url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            return result.get("memories", [])
        except Exception as e:
            logger.error(f"MemSync search failed: {e}")
            return []

    def add_memory(self, content: str, agent_id: str = "coincheckgo-sentinel", source: str = "analysis") -> bool:
        """
        Store a new memory (e.g., analysis result).
        """
        if not self.is_enabled():
            return False

        try:
            url = f"{self.BASE_URL}/memories"
            headers = {
                "X-API-Key": self.api_key,
                "Content-Type": "application/json"
            }
            # Ensure agent_id is a valid UUID
            # If default "coincheckgo-sentinel" is used, replace with a consistent UUID or random one
            real_agent_id = agent_id
            try:
                uuid.UUID(agent_id)
            except ValueError:
                # Fallback to a static UUID for this app instance if not a valid UUID
                real_agent_id = "00000000-0000-0000-0000-coincheckgo" 
                # Or just random:
                real_agent_id = str(uuid.uuid4())

            data = {
                "messages": [
                    {
                        "role": "user",
                        "content": content
                    }
                ],
                "agent_id": real_agent_id,
                "source": source
            }
            
            response = requests.post(url, json=data, headers=headers, timeout=5) # Reduced timeout
            
            if not response.ok:
                logger.error(f"MemSync API Error: {response.status_code} - {response.text}")
                return False

            logger.info("Successfully stored memory to MemSync")
            return True

        except requests.exceptions.RequestException as re:
            # Network error - generic warning, don't spam stack trace
            logger.warning(f"MemSync connection failed: {re}")
            return False
        except Exception as e:
            logger.error(f"MemSync add_memory unexpected error: {e}")
            return False
