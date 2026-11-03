"""Catch source changes that were not included in the offline release package."""
import ast
import hashlib
import json
from pathlib import Path
from package_runtime import source_digest

root=Path(__file__).resolve().parent
release=json.loads((root/'runtime.json').read_text())
assert source_digest()==release['sourceSha256'],'Fork source changed; rebuild frontend and runtime'
digest=hashlib.sha256((root/'runtime.tar.gz').read_bytes()).hexdigest()
assert digest==release['sha256'],'Runtime archive hash mismatch'
tree=ast.parse((root.parent/'deploy/home_assistant.py').read_text())
pin=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
    and any(isinstance(target,ast.Name) and target.id=='FORK_ARCHIVE_SHA' for target in node.targets))
assert pin==digest,'Root manager must pin the reviewed runtime archive'
print('Car Assistant source, runtime archive and trusted manager pin match')
