import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from control.service_watch import environment


class ServiceEnvironmentTests(unittest.TestCase):
    def test_settings_reload_and_shell_text_remains_plain_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);file=root/'private.env';marker=root/'should-not-exist'
            file.write_text("CAR_PANEL_TOKEN='sample token with spaces'\nCAR_VALUE='$(touch "+str(marker)+")'\n")
            with patch.dict(os.environ,{'CAR_REMOVED':'old-value'}):result=environment(file)
            self.assertEqual(result['CAR_PANEL_TOKEN'],'sample token with spaces')
            self.assertNotIn('CAR_REMOVED',result)
            self.assertFalse(marker.exists())
            file.write_text('CAR_PANEL_TOKEN=new-value\n')
            self.assertEqual(environment(file)['CAR_PANEL_TOKEN'],'new-value')
    def test_invalid_unquoted_setting_is_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'private.env';file.write_text('CAR_VALUE=not quoted text\n')
            with self.assertRaises(ValueError):environment(file)
