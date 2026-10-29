from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from . import DOMAIN
from .entity import CarEntity


async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([CarLocation(hass.data[DOMAIN][entry.entry_id],'location','Location')])


class CarLocation(CarEntity,TrackerEntity):
    _attr_source_type=SourceType.GPS
    @property
    def available(self):return super().available and bool(self.coordinator.data.get('gps'))
    @property
    def latitude(self):return (self.coordinator.data.get('gps') or {}).get('lat')
    @property
    def longitude(self):return (self.coordinator.data.get('gps') or {}).get('lon')
