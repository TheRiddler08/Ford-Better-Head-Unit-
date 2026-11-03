"""APT must acquire verified local archives, never raw relative dpkg filenames."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from deploy import offline_payload as payload


class OfflineAPTTests(unittest.TestCase):
    def test_local_acquisition_and_existing_package_preservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'pool/n/network-manager/gir.deb';source.parent.mkdir(parents=True);source.write_bytes(b'fixture')
            details={'suites':['noble','noble-updates','noble-security'],
                     'packages':[{'Package':'gir1.2-nm-1.0','Version':'1.46.0-1ubuntu2.8','Architecture':'amd64','Filename':'pool/n/network-manager/gir.deb'}]}
            calls=[]
            def apt(args,env,**kwargs):
                config=Path(env['APT_CONFIG']).read_text()
                sources=Path(re.search(r'Dir::Etc::sourcelist "([^"]+)"',config)[1]).read_text()
                self.assertIn('file:'+str(root),sources);self.assertNotIn('http:',sources);self.assertNotIn('https:',sources)
                self.assertIn('signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg',sources)
                self.assertIn('Acquire::Check-Valid-Until "true"',config)
                cache=Path(re.search(r'Dir::Cache::archives "([^"]+)"',config)[1])
                self.assertTrue(cache.is_absolute());self.assertTrue((cache/'gir1.2-nm-1.0_1.46.0-1ubuntu2.8_amd64.deb').is_file())
                calls.append(args)
            with patch.object(payload,'verify_repository',return_value=details) as verify,patch.object(payload,'run',side_effect=apt):payload.install_packages(root)
            verify.assert_called_once_with(root.resolve())
            self.assertEqual(calls[0],['apt-get','update'])
            self.assertIn('--no-upgrade',calls[1]);self.assertNotIn('--no-download',calls[1])
            self.assertNotIn('--allow-unauthenticated',calls[1])
