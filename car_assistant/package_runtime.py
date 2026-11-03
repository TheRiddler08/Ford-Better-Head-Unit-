"""Package the reviewed source fork and production frontend for offline delivery."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent


def source_digest():
    paths=[]
    for name in ('core/homeassistant','frontend/src','frontend/build-scripts',
                 'frontend/public','frontend/patches','frontend/translations/frontend'):
        paths.extend(p for p in (ROOT/'upstream'/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    paths.extend(ROOT/'upstream/frontend'/name for name in ('package.json','pnpm-lock.yaml',
        'pnpm-workspace.yaml','pyproject.toml','gulpfile.js','rspack.config.cjs','tsconfig.json'))
    digest=hashlib.sha256()
    for path in sorted(paths):
        if path.suffix in ('.pyc','.pyo'):continue
        digest.update(path.relative_to(ROOT).as_posix().encode()+b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def package():
    frontend = ROOT / 'upstream/frontend/hass_frontend'
    if not (frontend / 'frontend_latest').is_dir():
        raise ValueError('Build the production frontend first')
    sources = [(ROOT / 'upstream/core/homeassistant', 'usr/src/homeassistant/homeassistant'),
               (frontend, 'usr/local/lib/python3.14/site-packages/hass_frontend')]
    files = []
    for folder, prefix in sources:
        for path in sorted(folder.rglob('*')):
            if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            # Materialize upstream's service-worker alias as a regular file.
            if path.is_symlink() and not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError('Runtime symlink escapes package: ' + str(path))
            if path.is_file():
                files.append((path, prefix + '/' + path.relative_to(folder).as_posix()))
    files += [(ROOT / 'UPSTREAM.json', 'usr/share/car-assistant/UPSTREAM.json'),
              (ROOT / 'NOTICE.md', 'usr/share/car-assistant/NOTICE.md'),
              (ROOT / 'upstream/core/LICENSE.md', 'usr/share/car-assistant/CORE-LICENSE.md'),
              (ROOT / 'upstream/frontend/LICENSE.md', 'usr/share/car-assistant/FRONTEND-LICENSE.md')]
    target = ROOT / 'runtime.tar.gz'
    with target.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w|') as archive:
            for path, name in files:
                data = path.read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (ROOT / 'runtime.json').write_text(json.dumps({'schema': 1, 'sha256': digest,
        'sourceSha256':source_digest(), 'baseVersion': '2026.10.4',
        'frontendVersion': '20260930.5', 'files': len(files)}, indent=2) + '\n')
    import re
    manager=ROOT.parent/'deploy/home_assistant.py'
    updated,count=re.subn(r"^FORK_ARCHIVE_SHA=(?:None|'[0-9a-f]{64}')$",
        "FORK_ARCHIVE_SHA='"+digest+"'",manager.read_text(),flags=re.M)
    if count!=1:raise ValueError('Cannot pin the runtime in the trusted manager')
    manager.write_text(updated)
    print(digest)


if __name__ == '__main__':
    package()
