"""Integration test for component management system."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from provider.provider_repository import RepositoryClient
from provider.provider_lifecycle import ProviderLifecycleManager


def _check_repository_client():
    """Test repository client can access test repository."""
    print("Testing RepositoryClient...")

    test_repo_path = Path(__file__).parent.parent / 'test-provider-repository'
    if not test_repo_path.exists():
        print(f"  [WARN] Test repository not found at {test_repo_path}")
        return False

    repo_url = f"file:///{test_repo_path.as_posix()}"

    try:
        client = RepositoryClient(repo_url)

        # Test get_info
        info = client.get_info()
        assert info.repository_id == 'pla-test-repo'
        print(f"  [OK] Repository info loaded: {info.name}")

        # Test list_providers
        providers = client.list_providers()
        assert len(providers) > 0
        assert providers[0].provider_id == 'test-echo-provider'
        print(f"  [OK] Found {len(providers)} provider(s)")

        # Test get_provider_metadata
        metadata = client.get_provider_metadata('test-echo-provider')
        assert metadata.name == 'Test Echo Provider'
        print(f"  [OK] Provider metadata loaded")

        # Test list_versions
        versions = client.list_versions('test-echo-provider')
        assert len(versions) > 0
        assert versions[0].version == '1.0.0'
        print(f"  [OK] Found {len(versions)} version(s)")

        # Test download_package
        package = client.download_package('test-echo-provider', '1.0.0')
        assert package.provider_id == 'test-echo-provider'
        assert package.version == '1.0.0'
        print(f"  [OK] Package downloaded and verified")

        # Verify checksums
        package.verify_checksums()
        print(f"  [OK] All checksums verified")

        return True

    except Exception as e:
        print(f"  [FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def _check_lifecycle_manager():
    """Test lifecycle manager basic functionality."""
    print("\nTesting ProviderLifecycleManager...")

    # Create temporary directories for testing
    import tempfile
    temp_dir = Path(tempfile.mkdtemp())

    try:
        component_root = temp_dir / 'components'
        manifest_dir = component_root / 'provider_manifests'
        manifest_dir.mkdir(parents=True, exist_ok=True)

        # Create dummy uv.exe
        uv_exe = component_root / 'tools' / 'uv.exe'
        uv_exe.parent.mkdir(parents=True, exist_ok=True)
        uv_exe.write_text('dummy')

        manager = ProviderLifecycleManager(
            component_root=component_root,
            manifest_dir=manifest_dir,
            uv_exe=uv_exe,
            python_version='3.11.9'
        )

        print(f"  [OK] Lifecycle manager initialized")

        # Test list_installed_providers (should be empty)
        installed = manager.list_installed_providers()
        assert len(installed) == 0
        print(f"  [OK] No providers installed initially")

        # Test get_provider_state (should return None)
        state = manager.get_provider_state('nonexistent')
        assert state is None
        print(f"  [OK] Nonexistent provider returns None")

        return True

    except Exception as e:
        print(f"  [FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def _check_components_integration():
    """Test ComponentProject integration."""
    print("\nTesting ComponentProject integration...")

    import tempfile
    temp_dir = Path(tempfile.mkdtemp())

    try:
        from desktop_runtime.components import ComponentProject

        data_dir = temp_dir / 'data'
        resources_dir = temp_dir / 'resources'
        data_dir.mkdir(parents=True)
        resources_dir.mkdir(parents=True)

        project = ComponentProject(data_dir, resources_dir)

        print(f"  [OK] ComponentProject initialized")

        # Test list_repositories (should be empty)
        repos = project.list_repositories()
        assert 'repositories' in repos
        assert len(repos['repositories']) == 0
        print(f"  [OK] Empty repository list")

        # Test discover_providers (should handle no repositories)
        result = project.discover_providers()
        assert 'providers' in result
        print(f"  [OK] Discovery with no repositories handled")

        return True

    except Exception as e:
        print(f"  [FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    """Run all integration tests."""
    print("="*60)
    print("Component Management System - Integration Tests")
    print("="*60)

    results = []

    # Test 1: Repository Client
    results.append(('RepositoryClient', _check_repository_client()))

    # Test 2: Lifecycle Manager
    results.append(('LifecycleManager', _check_lifecycle_manager()))

    # Test 3: Components Integration
    results.append(('ComponentProject', _check_components_integration()))

    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n[OK] All integration tests passed!")
        return 0
    else:
        print(f"\n[FAIL] {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())


# Pytest must fail when a legacy diagnostic returns False.
def test_repository_client():
    assert _check_repository_client() is True

def test_lifecycle_manager():
    assert _check_lifecycle_manager() is True

def test_components_integration():
    assert _check_components_integration() is True

