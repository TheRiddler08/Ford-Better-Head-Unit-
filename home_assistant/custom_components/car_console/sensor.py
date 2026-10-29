from homeassistant.components.sensor import SensorEntity, SensorStateClass
from . import DOMAIN
from .entity import CarEntity

METRICS=[('speed','Speed','km/h','speed','mdi:speedometer'),
 ('rpm','RPM','rpm',None,'mdi:engine'),('coolant','Coolant','°C','temperature','mdi:coolant-temperature'),
 ('voltage','ECU voltage','V','voltage','mdi:car-battery'),('fuel','Raw fuel level','%',None,'mdi:gas-station'),
 ('load','Engine load','%',None,'mdi:engine'),('throttle','Throttle','%',None,'mdi:car-cruise-control'),
 ('intake','Intake temperature','°C','temperature','mdi:thermometer')]


async def async_setup_entry(hass,entry,async_add_entities):
    coordinator=hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CarSensor(coordinator,*row) for row in METRICS])


class CarSensor(CarEntity,SensorEntity):
    _attr_state_class=SensorStateClass.MEASUREMENT
    def __init__(self,coordinator,key,name,unit,device_class,icon):
        super().__init__(coordinator,key,name)
        self.key=key
        self._attr_native_unit_of_measurement=unit
        self._attr_device_class=device_class
        self._attr_icon=icon
    @property
    def available(self):
        return super().available and self.coordinator.data['metrics'].get(self.key,{}).get('fresh',False)
    @property
    def native_value(self):return self.coordinator.data['metrics'].get(self.key,{}).get('value')
    @property
    def extra_state_attributes(self):
        return {'recorded_at':self.coordinator.data['metrics'].get(self.key,{}).get('lastSeen'),
                'source':'Local recorder; vehicle read-only'}
