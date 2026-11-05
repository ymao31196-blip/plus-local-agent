"""Regression contract for explicit Office custom-component migration."""
import json
import shutil
from pathlib import Path

import pytest

from scripts.office_component_migrate import FILES, REPOSITORY, migrate, migration_preview


def _layout(tmp_path):
    app = tmp_path / "io.pla.desktop"
    components = app / "components"
    for relative in FILES:
        dest = components / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / relative, dest)
    manifest = components / FILES[0]
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["tool_allowlist"] = ["wps_macro", "artifact_build_overlay"]
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    return app, components


def test_office_migration_requires_current_hash_and_backs_up_originals(tmp_path):
    app, components = _layout(tmp_path)
    preview = migration_preview(components)
    assert preview["applied"] is False
    with pytest.raises(ValueError, match="manifest changed"):
        migrate(components, REPOSITORY, "0"*64, app / "office-migration-backups")
    assert "wps_macro" in (components / FILES[0]).read_text(encoding="utf-8")
    result = migrate(components, REPOSITORY, preview["expected_sha256"],
                     app / "office-migration-backups")
    assert result["applied"] is True
    for relative in FILES:
        assert (components / relative).read_bytes() == (REPOSITORY / relative).read_bytes()
        assert (Path(result["backup_path"]) / relative).is_file()
    original = json.loads((Path(result["backup_path"]) / FILES[0]).read_text(encoding="utf-8"))
    assert original["tool_allowlist"] == ["wps_macro", "artifact_build_overlay"]


def test_office_migration_detects_source_change_before_write(tmp_path):
    app, components = _layout(tmp_path)
    preview = migration_preview(components)
    path = components / FILES[0]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="preview again"):
        migrate(components, REPOSITORY, preview["expected_sha256"],
                app / "office-migration-backups")
