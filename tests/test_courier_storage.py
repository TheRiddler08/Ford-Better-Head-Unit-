"""The Duet courier cache must stay bounded across repeated signed refreshes."""
import hashlib
import json
import lzma
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from deploy import runtime_assets as assets


def sha(data):return hashlib.sha256(data).hexdigest()


class FakeArchive(assets.Downloads):
    """Serves a tiny signed-looking Ubuntu mirror from memory."""
    def __init__(self,cache,mirror):
        super().__init__(cache);self.mirror=mirror;self.requests=[]
    def fetch(self,url,expected=None,headers=None,limit=assets.MAX_DOWNLOAD):
        if expected and (self.cache/expected).exists():
            self.keep(self.cache/expected);return self.cache/expected
        self.requests.append(url)
        data=self.mirror[url.split('/ubuntu/',1)[1]]
        target=self.cache/sha(data);target.write_bytes(data);self.keep(target);return target


def mirror(revision,deb=b'deb payload'):
    files={}
    packages=''.join('Package: %s\nVersion: 1\nArchitecture: amd64\nFilename: pool/%s.deb\nSHA256: %s\nSize: 1\n\n'%(name,name,sha(deb+name.encode())) for name in assets.ROOT_PACKAGES).encode()
    for name in assets.ROOT_PACKAGES:files['pool/%s.deb'%name]=deb+name.encode()
    for suite in ('noble','noble-updates','noble-security'):
        lines=[]
        for component in ('main','universe'):
            raw=packages if component=='main' else b''
            packed=lzma.compress(raw)
            files['dists/%s/%s/binary-amd64/Packages.xz'%(suite,component)]=packed
            lines+=[' %s 1 %s/binary-amd64/Packages'%(sha(raw),component),' %s 1 %s/binary-amd64/Packages.xz'%(sha(packed),component)]
        files['dists/%s/InRelease'%suite]=('Origin: Ubuntu\nRevision: %s\nSHA256:\n'%revision+'\n'.join(lines)+'\n').encode()
    return files


class CourierStorageTests(unittest.TestCase):
    def test_index_refresh_replaces_previous_archive_and_reuses_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp)
            first=FakeArchive(cache/'objects',mirror('1'))
            old,_=assets.ubuntu_archive(first,'24.04','noble',cache/'ubuntu')
            assets.prune(cache,{'ubuntu-24.04.tar.gz':old},first)
            # A security pocket refresh signs a new index with identical packages.
            second=FakeArchive(cache/'objects',mirror('2'))
            new,_=assets.ubuntu_archive(second,'24.04','noble',cache/'ubuntu')
            self.assertNotEqual(old,new)
            self.assertFalse(any('/pool/' in url for url in second.requests),'unchanged packages are reused')
            freed=assets.prune(cache,{'ubuntu-24.04.tar.gz':new},second)
            self.assertGreater(freed,0)
            self.assertEqual(sorted(p.name for p in (cache/'ubuntu').iterdir()),sorted([new.name,new.with_suffix('.json').name]))
            for name in assets.ROOT_PACKAGES:self.assertTrue((cache/'objects'/sha(b'deb payload'+name.encode())).exists())
            # Superseded signed indexes are gone; the current ones remain.
            stale=sha(mirror('1')['dists/noble/InRelease'])
            self.assertFalse((cache/'objects'/stale).exists())
            self.assertTrue((cache/'objects'/sha(mirror('2')['dists/noble/InRelease'])).exists())
            # Repeated unchanged checks do not grow the cache.
            sizes=sum(p.stat().st_size for p in cache.rglob('*') if p.is_file())
            third=FakeArchive(cache/'objects',mirror('2'))
            same,_=assets.ubuntu_archive(third,'24.04','noble',cache/'ubuntu')
            assets.prune(cache,{'ubuntu-24.04.tar.gz':same},third)
            self.assertEqual(same,new)
            self.assertEqual(sizes,sum(p.stat().st_size for p in cache.rglob('*') if p.is_file()))

    def test_superseded_image_and_released_layers_are_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp);images=cache/'images';objects=cache/'objects'
            images.mkdir();objects.mkdir()
            for name in ('old.tar.gz','old.json','new.tar.gz','new.json'):(images/name).write_bytes(b'x'*10)
            downloads=assets.Downloads(objects)
            for name in ('manifest','layer'):(objects/name).write_bytes(b'y'*10);downloads.keep(objects/name)
            downloads.release(objects/'layer')
            (objects/'orphan').write_bytes(b'z')
            assets.prune(cache,{'ha-image.tar.gz':images/'new.tar.gz'},downloads)
            self.assertEqual(sorted(p.name for p in images.iterdir()),['new.json','new.tar.gz'])
            self.assertEqual([p.name for p in objects.iterdir()],['manifest'])

    def test_power_loss_leftovers_are_removed_but_live_json_writes_are_not(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp);(state/'tmpab12cd_9').mkdir();(state/'tmpab12cd_9'/'ready.zip').write_bytes(b'p'*100)
            (state/'tmpq1w2e3r4').write_text('{}')  # concurrent save_json in progress
            (state/'ready.zip').write_bytes(b'r');(state/'cache.git').mkdir()
            self.assertEqual(assets.stale_temporaries(state,directories_only=True),100)
            self.assertEqual(sorted(p.name for p in state.iterdir()),['cache.git','ready.zip','tmpq1w2e3r4'])
            self.assertEqual(assets.stale_temporaries(state),2)

    def test_low_space_fails_before_filling_the_display(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(assets.shutil,'disk_usage',return_value=types.SimpleNamespace(free=assets.MIN_FREE+10)):
                assets.ensure_space(temp,10)
                with self.assertRaisesRegex(ValueError,'previous package retained'):assets.ensure_space(temp,11)

    def test_failed_build_keeps_partial_downloads_for_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp);(cache/'objects').mkdir(parents=True);(cache/'objects'/'layer').write_bytes(b'l')
            with patch.object(assets,'image_archive',side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):assets.build(cache,{'schema':1,'image':'x','ubuntu':{}})
            self.assertTrue((cache/'objects'/'layer').exists())


if __name__=='__main__':unittest.main()
