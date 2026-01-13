"""
Mock Home Assistant Objects for Testing

Since we don't want to require a full Home Assistant installation for testing,
this module provides mock implementations of HA entities and state objects.
"""

from typing import Optional, Any, Dict, Callable
from dataclasses import dataclass
import asyncio


@dataclass
class MockState:
    """Mock Home Assistant State object."""
    entity_id: str
    state: str
    attributes: Dict[str, Any]
    context: Optional[str] = None


@dataclass
class MockEvent:
    """Mock Home Assistant state change event."""
    data: Dict[str, Any]


class MockHass:
    """
    Mock Home Assistant core instance.
    
    Provides minimal functionality needed for testing:
    - Storing and retrieving entity states
    - Firing state change events
    - Calling services (mocked)
    """
    
    def __init__(self):
        """Initialize mock Home Assistant instance."""
        self.states: Dict[str, MockState] = {}
        self.services_called: list[Dict[str, Any]] = []
        self.event_listeners: list[Callable] = []
        self.data: Dict[str, Any] = {}
    
    def set_state(self, entity_id: str, state: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """
        Set an entity's state.
        
        Args:
            entity_id: The entity to set
            state: The state value
            attributes: Optional attributes dict
        """
        if attributes is None:
            attributes = {}
        
        self.states[entity_id] = MockState(
            entity_id=entity_id,
            state=state,
            attributes=attributes
        )
    
    def get_state(self, entity_id: str) -> Optional[MockState]:
        """Get an entity's current state."""
        return self.states.get(entity_id)
    
    async def fire_state_change_event(self, entity_id: str, old_state: Optional[MockState], new_state: Optional[MockState]) -> None:
        """
        Fire a state change event and notify all listeners.
        
        Args:
            entity_id: Entity that changed
            old_state: Previous state
            new_state: New state
        """
        event = MockEvent(data={
            'entity_id': entity_id,
            'old_state': old_state,
            'new_state': new_state
        })
        
        # Call all registered event listeners
        for listener in self.event_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                print(f"Error in event listener: {e}")
    
    async def services_async_call(self, domain: str, service: str, service_data: Optional[Dict[str, Any]] = None, blocking: bool = False) -> None:
        """
        Mock service call.
        
        Args:
            domain: Service domain (e.g., 'number', 'climate')
            service: Service name (e.g., 'set_value')
            service_data: Service parameters
            blocking: Whether to wait for service completion
        """
        call_record = {
            'domain': domain,
            'service': service,
            'service_data': service_data or {},
            'blocking': blocking
        }
        self.services_called.append(call_record)
    
    def clear_service_calls(self) -> None:
        """Clear the service call history."""
        self.services_called.clear()
    
    def get_service_calls(self, domain: str, service: str) -> list[Dict[str, Any]]:
        """Get all service calls for a specific domain and service."""
        return [
            call for call in self.services_called
            if call['domain'] == domain and call['service'] == service
        ]
    
    def last_service_call(self, domain: str, service: str) -> Optional[Dict[str, Any]]:
        """Get the last service call for a domain and service."""
        calls = self.get_service_calls(domain, service)
        return calls[-1] if calls else None


class MockClimateEntity:
    """Mock climate entity (TRV valve)."""
    
    def __init__(self, entity_id: str, name: str = "Mock Climate"):
        """
        Initialize mock climate entity.
        
        Args:
            entity_id: The entity ID
            name: Friendly name
        """
        self.entity_id = entity_id
        self.name = name
        self.current_temperature = 20.0
        self.target_temperature = 20.0
        self.hvac_action = "idle"
    
    def to_state_dict(self) -> tuple[str, Dict[str, Any]]:
        """Convert to Home Assistant state dict."""
        return (self.entity_id, {
            'current_temperature': self.current_temperature,
            'target_temp': self.target_temperature,  # HA uses "target_temp", not "target_temperature"
            'hvac_action': self.hvac_action,
        })


class MockSensorEntity:
    """Mock sensor entity (e.g., temperature sensor)."""
    
    def __init__(self, entity_id: str, value: float, unit: str = "°C"):
        """
        Initialize mock sensor.
        
        Args:
            entity_id: The entity ID
            value: Sensor value
            unit: Unit of measurement
        """
        self.entity_id = entity_id
        self.value = value
        self.unit = unit
    
    def to_state(self) -> str:
        """Get state as string."""
        return str(self.value)


class MockNumberEntity:
    """Mock number entity (for flow temperature control)."""
    
    def __init__(self, entity_id: str, value: float = 5.0, min_value: float = 5.0, max_value: float = 80.0):
        """
        Initialize mock number entity.
        
        Args:
            entity_id: The entity ID
            value: Current value
            min_value: Minimum allowed value
            max_value: Maximum allowed value
        """
        self.entity_id = entity_id
        self.value = value
        self.min_value = min_value
        self.max_value = max_value
    
    def set_value(self, value: float) -> None:
        """Set the value, with bounds checking."""
        self.value = max(self.min_value, min(self.max_value, value))
