from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import DOMAIN


class CarEntity(CoordinatorEntity):
    _attr_has_entity_name=True
    def __init__(self,coordinator,key,name):
        super().__init__(coordinator)
        self._attr_unique_id='car_console_'+key
        self._attr_name=name
        self._attr_device_info={'identifiers':{(DOMAIN,'tiny')},'name':'Car',
                               'manufacturer':'Drive Console','model':'Read-only OBD and GPS bridge'}
