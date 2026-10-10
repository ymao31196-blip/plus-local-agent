import importlib.util
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
