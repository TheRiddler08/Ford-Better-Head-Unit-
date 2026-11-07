import contextlib,io,json,subprocess,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
from dev import setup_us_routing as setup

class RoutingSetup(unittest.TestCase):
    def test_default_full_usa_refuses_undersized_build_host_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory, patch('sys.argv',['routing_setup','--directory',directory]),patch.object(setup.shutil,'disk_usage',return_value=types.SimpleNamespace(free=200*1024**3)),patch.object(setup.os,'sysconf',side_effect=lambda key:4096 if key=='SC_PAGE_SIZE' else 14*1024**3//4096),patch.object(setup,'dataset') as download,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:setup.main()
            self.assertEqual(error.exception.code,2);download.assert_not_called()
    def test_prepared_full_usa_pack_can_start_offline_on_small_host_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);metadata={'regions':['us'],'image':'ghcr.io/project-osrm/osrm-backend@sha256:'+'a'*64,'imageId':'sha256:'+'b'*64}
            (root/'prepared.json').write_text(json.dumps(metadata))
            for suffix in ('partition','cells','mldgr'):(root/('region.osrm.'+suffix)).touch()
            calls=[]
            def execute(args,**_):calls.append(args);return ''
            def run(args,**_):return subprocess.CompletedProcess(args,1 if 'container' in args else 0,stdout='',stderr='')
            client=types.SimpleNamespace(open=lambda *a,**k:contextlib.nullcontext(types.SimpleNamespace(status=200)))
            with patch('sys.argv',['routing_setup','--serve-only','--offline','--directory',directory]),patch.object(setup,'execute',side_effect=execute),patch.object(setup.subprocess,'run',side_effect=run),patch.object(setup,'build_opener',return_value=client),patch.object(setup,'dataset') as download,contextlib.redirect_stdout(io.StringIO()):
                setup.main()
            download.assert_not_called()
            self.assertFalse(any('pull' in args for args in calls))
            launch=next(args for args in calls if 'osrm-routed' in args)
            self.assertIn('127.0.0.1:5000:5000',launch);self.assertIn('--mmap=true',launch);self.assertIn(metadata['imageId'],launch)
