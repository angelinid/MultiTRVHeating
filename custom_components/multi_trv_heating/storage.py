"""
Persistent state storage for MultiTRVHeating entities.

Uses Home Assistant's storage system to save and restore entity states
across restarts.
"""

import logging
from typing import Optional, Dict, Any

try:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store
except ImportError:
    # For testing without Home Assistant
    HomeAssistant = None
    Store = None

_LOGGER = logging.getLogger("don_controller")

DOMAIN = "multi_trv_heating"
STORAGE_VERSION = 1


class StateStorage:
    """Manages persistent state storage for MultiTRVHeating entities."""
    
    def __init__(self, hass: Optional["HomeAssistant"] = None) -> None:
        """
        Initialize state storage.
        
        Args:
            hass: Home Assistant instance
        """
        self.hass = hass
        self.store = None
        self.data: Dict[str, Any] = {}
        
        if hass and Store:
            self.store = Store(
                hass,
                STORAGE_VERSION,
                f"{DOMAIN}_state_storage"
            )
    
    async def async_load(self) -> Dict[str, Any]:
        """
        Load stored state from disk.
        
        Returns:
            Dict with stored entity states
        """
        if not self.store:
            return {}
        
        try:
            loaded_data = await self.store.async_load()
            self.data = loaded_data or {}
            _LOGGER.debug("Loaded stored state: %s", list(self.data.keys()))
            return self.data
        except Exception as e:
            _LOGGER.error("Error loading stored state: %s", e)
            return {}
    
    async def async_save(self) -> None:
        """Save current state to disk."""
        if not self.store:
            return
        
        try:
            await self.store.async_save(self.data)
            _LOGGER.debug("Saved state to storage: %s", list(self.data.keys()))
        except Exception as e:
            _LOGGER.error("Error saving state to storage: %s", e)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a stored value."""
        return self.data.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set a stored value."""
        self.data[key] = value
    
    async def async_set_and_save(self, key: str, value: Any) -> None:
        """Set value and immediately save to disk."""
        self.set(key, value)
        await self.async_save()


# Global storage instance
_storage: Optional[StateStorage] = None


def get_storage() -> Optional[StateStorage]:
    """Get the global storage instance."""
    return _storage


def set_storage(storage: StateStorage) -> None:
    """Set the global storage instance."""
    global _storage
    _storage = storage
