from homeassistant import config_entries
from . import DOMAIN


class CarConsoleFlow(config_entries.ConfigFlow,domain=DOMAIN):
    VERSION=1
    async def async_step_import(self,user_input=None):
        return await self.async_step_user(user_input)
    async def async_step_user(self,user_input=None):
        await self.async_set_unique_id('local_tiny')
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title='Car Console',data={})
