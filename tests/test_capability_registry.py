import pytest

from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from fake_capability_provider import FakeCapabilityProvider


def make_capability(provider: str, name: str, *, title: str | None = None):
    return CapabilityDescriptor(
        id=f"{provider}.{name}",
        provider_id=provider,
        remote_name=name.replace(".", "_"),
        title=title or name,
        description=f"Capability {name}",
        input_schema={"type": "object", "properties": {}},
        tags=tuple(name.split(".")),
    )


def test_fake_provider_register_search_and_describe():
    registry = CapabilityRegistry()
    provider = FakeCapabilityProvider()
    registry.register_provider(provider.provider_id, provider.discover())

    result = registry.search("pdf compress")
    assert result["match_count"] == 1
    assert result["capabilities"][0]["id"] == "fake.pdf.compress"
    assert result["capabilities"][0]["available"] is True

    detail = registry.describe("fake.pdf.compress")
    assert detail["remote_name"] == "compress_pdf"
    assert detail["artifact_inputs"] == ["input_artifact"]
    assert detail["artifact_outputs"] is True
    assert detail["risk_level"] == "write_local"
    assert detail["input_schema"]["required"] == ["input_artifact"]
    assert detail["available"] is True


def test_provider_refresh_replaces_old_capabilities():
    registry = CapabilityRegistry()
    registry.register_provider("alpha", [
        make_capability("alpha", "one"),
        make_capability("alpha", "two"),
    ])
    registry.register_provider("alpha", [make_capability("alpha", "three")])

    assert registry.search("", provider_id="alpha")["match_count"] == 1
    assert registry.search("", provider_id="alpha")["capabilities"][0]["id"] == "alpha.three"
    with pytest.raises(ValueError, match="Unknown capability"):
        registry.describe("alpha.one")


def test_provider_disable_hides_capabilities_by_default():
    registry = CapabilityRegistry()
    registry.register_provider("alpha", [make_capability("alpha", "one")])
    registry.set_provider_enabled("alpha", False)

    assert registry.search("")["match_count"] == 0
    hidden = registry.search("", include_unavailable=True)
    assert hidden["match_count"] == 1
    assert hidden["capabilities"][0]["available"] is False
    assert registry.describe("alpha.one")["available"] is False


def test_registry_scales_to_100_capabilities_and_limits_results():
    registry = CapabilityRegistry()
    registry.register_provider(
        "bulk",
        [make_capability("bulk", f"tool{i:03d}") for i in range(100)],
    )

    result = registry.search("", limit=17)
    assert result["match_count"] == 100
    assert result["returned_count"] == 17
    assert result["truncated"] is True
    assert result["capabilities"][0]["id"] == "bulk.tool000"


def test_registry_rejects_duplicate_ids_from_provider():
    registry = CapabilityRegistry()
    capability = make_capability("alpha", "one")
    with pytest.raises(ValueError, match="duplicate capability ids"):
        registry.register_provider("alpha", [capability, capability])


def test_descriptor_requires_provider_namespace_and_valid_risk():
    with pytest.raises(ValueError, match="namespaced"):
        CapabilityDescriptor(
            id="beta.tool",
            provider_id="alpha",
            remote_name="tool",
            title="Tool",
            description="Tool",
            input_schema={},
        )
    with pytest.raises(ValueError, match="risk_level"):
        CapabilityDescriptor(
            id="alpha.tool",
            provider_id="alpha",
            remote_name="tool",
            title="Tool",
            description="Tool",
            input_schema={},
            risk_level="unknown",
        )


def test_unknown_provider_and_capability_are_errors():
    registry = CapabilityRegistry()
    with pytest.raises(ValueError, match="Unknown provider"):
        registry.search("", provider_id="missing")
    with pytest.raises(ValueError, match="Unknown capability"):
        registry.describe("missing.tool")
