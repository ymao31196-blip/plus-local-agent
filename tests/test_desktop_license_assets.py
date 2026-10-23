import importlib.util
import hashlib
import subprocess
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'desktop_license_packager', Path(__file__).parents[1] / 'desktop/packaging/licenses.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.mark.parametrize('filename,reason', [('LICENSE.txt', 'hash mismatch'), ('../outside.txt', 'inside reviewed assets')])
def test_unreviewed_license_asset_is_rejected_before_delivery(tmp_path, filename, reason):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'LICENSE.txt').write_text('Changed after review')
    (tmp_path / 'outside.txt').write_text('Must not be copied')
    (assets / 'manifest.json').write_text(json.dumps({'licenses': [{
        'kind': 'python', 'name': 'example', 'version': '1.0', 'file': filename,
        'sha256': '0' * 64, 'source': 'https://example.invalid/reviewed-license',
    }]}))
    output = tmp_path / 'delivery'
    with pytest.raises(ValueError, match=reason):
        packager.supplemental_license('python', 'example', '1.0', output, assets)
    assert not output.exists()


def test_reviewed_license_digests_survive_windows_checkout_filters():
    # GitHub Windows checkout can use core.autocrlf=true. A supplemental
    # license is reviewed by its exact bytes; changing only its line endings
    # breaks the pinned digest and must be prevented at checkout.
    root = Path(__file__).resolve().parents[1]
    assets = root / 'desktop/packaging/license-assets'
    entries = json.loads((assets / 'manifest.json').read_text(encoding='utf-8'))['licenses']
    files = {entry['file']: entry['sha256'] for entry in entries}
    for filename, expected_hash in files.items():
        path = assets / filename
        relative = path.relative_to(root).as_posix()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
        exported = subprocess.run(
            ['git', '-c', 'core.autocrlf=true', 'cat-file', '--filters',
             f'--path={relative}', f'HEAD:{relative}'],
            cwd=root, check=True, capture_output=True, timeout=15,
        ).stdout
        assert hashlib.sha256(exported).hexdigest() == expected_hash, (
            f'Windows checkout changed reviewed license bytes: {filename}'
        )
