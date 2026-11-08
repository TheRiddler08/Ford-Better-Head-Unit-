"""The Tiny updater uses host Python, without HA's container-only dependencies."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]

class OptionalHostDependencies(unittest.TestCase):
    def test_host_validation_without_aiohttp_skips_only_optional_http_tests(self):
        script='''
import importlib.abc,sys,unittest
class NoAiohttp(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='aiohttp' or fullname.startswith('aiohttp.'):
            raise ModuleNotFoundError('No module named aiohttp',name='aiohttp')
sys.meta_path.insert(0,NoAiohttp())
loader=unittest.TestLoader();suite=unittest.TestSuite()
for pattern in ('test_car_tile_proxy.py','test_navigation.py'):
    suite.addTests(loader.discover('tests',pattern=pattern))
result=unittest.TextTestRunner().run(suite)
assert result.wasSuccessful()
assert len(result.skipped)==2, result.skipped
'''
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
