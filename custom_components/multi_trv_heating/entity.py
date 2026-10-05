"""Helpers shared by the sensor, switch, number and select platforms."""

import logging
from typing import Any, Optional

try:
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:
    # For testing without Home Assistant
    DeviceInfo = None

try:
    from .const import DOMAIN, LOGGER_NAME
    from .storage import get_storage
except ImportError:
    from const import DOMAIN, LOGGER_NAME
    from storage import get_storage

_LOGGER = logging.getLogger(LOGGER_NAME)


def prefixed_unique_id(entry_id: Optional[str], unique_id: str) -> str:
    """Prefix a unique ID with the config entry ID (if any)."""
    return f"{entry_id}_{unique_id}" if entry_id else unique_id


def zone_slug(zone_name: str) -> str:
    """Zone name as used inside unique IDs ('Living Room' -> 'living_room')."""
    return zone_name.lower().replace(" ", "_")


def controller_device_info(
    entry_id: str,
    name: str = "Multi-TRV Heating Controller",
    manufacturer: str = "Multi-TRV Heating",
    model: str = "System Controller",
) -> Optional[Any]:
    """DeviceInfo grouping the controller-level entities (None outside HA)."""
    if DeviceInfo is None:
        return None
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_controller")},
        name=name,
        manufacturer=manufacturer,
        model=model,
    )


def zone_device_info(entry_id: str, zone_entity_id: str, zone_name: str) -> Optional[Any]:
    """DeviceInfo grouping one zone's entities (None outside HA)."""
    if DeviceInfo is None:
        return None
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{zone_entity_id.replace('.', '_')}")},
        name=zone_name,
        manufacturer="Multi-TRV Heating",
        model="Zone Controller",
    )


def get_controller(hass, entry):
    """Return the MasterController for a config entry, or None (logged) if missing."""
    controller = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if controller is None:
        _LOGGER.error("No controller found for entry %s", entry.entry_id)
    return controller


class PersistentEntityMixin:
    """
    Persist an entity's setting in StateStorage under '<STORAGE_PREFIX>_<unique_id>'.

    The key format is part of the on-disk storage and must not change.
    """

    STORAGE_PREFIX: str = ""

    @property
    def _storage_key(self) -> str:
        return f"{self.STORAGE_PREFIX}_{self._attr_unique_id}"

    def _restore_stored(self) -> Any:
        """Stored value for this entity, or None if nothing is stored."""
        storage = get_storage()
        return storage.get(self._storage_key) if storage else None

    async def _async_persist(self, value: Any) -> None:
        storage = get_storage()
        if storage:
            await storage.async_set_and_save(self._storage_key, value)
