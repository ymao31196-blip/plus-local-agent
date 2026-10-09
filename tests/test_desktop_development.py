import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from desktop_runtime.development import prepare_source


def snapshot(resources, files):
    resources.mkdir()
    with zipfile.ZipFile(resources / 'developer-source.zip', 'w') as archive:
        rows = []
        for path, content in files.items():
            archive.writestr(path, content)
            rows.append({'path': path, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
        archive.writestr('PLA-SOURCE-MANIFEST.json', json.dumps({'source_commit': 'reviewed-commit', 'files': rows}))


def test_reviewed_snapshot_prepare_binds_target_and_keeps_default_grant_off(tmp_path):
    resources, target, other = tmp_path / 'resources', tmp_path / 'source', tmp_path / 'other'
    target.mkdir(); other.mkdir()
    snapshot(resources, {'src/server.py': b'reviewed source'})
    preview = prepare_source(tmp_path / 'data', resources, str(target), False, None)
    assert list(target.iterdir()) == []
    with pytest.raises(ValueError, match='exact source snapshot'):
        prepare_source(tmp_path / 'data', resources, str(other), True, preview['sha256'])
    result = prepare_source(tmp_path / 'data', resources, str(target), True, preview['sha256'])
    assert result['prepared'] and not result['development_enabled']
    assert (target / 'src/server.py').read_bytes() == b'reviewed source'
    with pytest.raises(ValueError, match='empty directory'):
        prepare_source(tmp_path / 'data', resources, str(target), True, preview['sha256'])
    assert (target / 'src/server.py').read_bytes() == b'reviewed source'


def test_source_snapshot_rejects_traversal_before_writing_anything(tmp_path):
    resources, target = tmp_path / 'resources', tmp_path / 'source'
    target.mkdir()
    snapshot(resources, {'../outside.txt': b'escape'})
    with pytest.raises(ValueError, match='Invalid source snapshot path'):
        prepare_source(tmp_path / 'data', resources, str(target), True, 'any')
    assert list(target.iterdir()) == [] and not (tmp_path / 'outside.txt').exists()
