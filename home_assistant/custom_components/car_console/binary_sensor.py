from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from . import DOMAIN
from .entity import CarEntity


async def async_setup_entry(hass,entry,async_add_entities):
    coordinator=hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CarStatus(coordinator,'obd','OBD connection'),CarStatus(coordinator,'gps','GPS fix')])


class CarStatus(CarEntity,BinarySensorEntity):
    _attr_device_class=BinarySensorDeviceClass.CONNECTIVITY
    def __init__(self,coordinator,key,name):super().__init__(coordinator,key,name);self.key=key
    @property
    def is_on(self):
        return bool(self.coordinator.data.get('obdFresh' if self.key=='obd' else 'gps'))
