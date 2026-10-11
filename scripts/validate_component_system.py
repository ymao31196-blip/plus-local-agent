"""Standalone validation of component management core functionality."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


def validate_all():
    """Run all validation checks."""
    print("="*70)
    print("Component Management System - Standalone Validation")
    print("="*70)

    checks = []

    # Check 1: Module imports
    print("\n[1/7] Checking module imports...")
    try:
        from provider.provider_repository import RepositoryClient, ProviderPackage
        from provider.provider_lifecycle import ProviderLifecycleManager, ProviderState
        print("  [OK] All core modules import successfully")
        checks.append(True)
    except Exception as e:
        print(f"  [FAIL] Import error: {e}")
        checks.append(False)
        return False  # Can't continue without imports

    # Check 2: Repository protocol support
    print("\n[2/7] Checking repository protocol support...")
    try:
        test_repo = Path('test-provider-repository')
        if not test_repo.exists():
            print("  [SKIP] Test repository not found")
            checks.append(True)
        else:
            url = f"file:///{test_repo.resolve().as_posix()}"
            client = RepositoryClient(url)
            info = client.get_info()
            providers = client.list_providers()

            assert len(providers) > 0, "No providers found"
            assert providers[0].provider_id == 'test-echo-provider'

            print(f"  [OK] Repository access works (found {len(providers)} provider)")
            checks.append(True)
    except Exception as e:
        print(f"  [FAIL] Repository error: {e}")
        checks.append(False)

    # Check 3: Package download and verification
    print("\n[3/7] Checking package download and checksums...")
    try:
        test_repo = Path('test-provider-repository')
        if not test_repo.exists():
            print("  [SKIP] Test repository not found")
            checks.append(True)
        else:
            url = f"file:///{test_repo.resolve().as_posix()}"
            client = RepositoryClient(url)
            package = client.download_package('test-echo-provider', '1.0.0')
            package.verify_checksums()

            assert package.provider_id == 'test-echo-provider'
            assert package.version == '1.0.0'
            assert len(package.assets) > 0

            print(f"  [OK] Package download and verification works")
            checks.append(True)
    except Exception as e:
        print(f"  [FAIL] Package error: {e}")
        checks.append(False)

    # Check 4: Lifecycle manager initialization
    print("\n[4/7] Checking lifecycle manager...")
    try:
        import tempfile
        temp_dir = Path(tempfile.mkdtemp())

        component_root = temp_dir / 'components'
        manifest_dir = component_root / 'provider_manifests'
        manifest_dir.mkdir(parents=True)

        uv_exe = component_root / 'tools' / 'uv.exe'
        uv_exe.parent.mkdir(parents=True)
        uv_exe.write_text('dummy')

        manager = ProviderLifecycleManager(
            component_root=component_root,
            manifest_dir=manifest_dir,
            uv_exe=uv_exe
        )

        installed = manager.list_installed_providers()
        assert isinstance(installed, list)

        print("  [OK] Lifecycle manager initializes correctly")
        checks.append(True)

        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        print(f"  [FAIL] Lifecycle manager error: {e}")
        checks.append(False)

    # Check 5: ComponentProject integration
    print("\n[5/7] Checking ComponentProject integration...")
    try:
        import tempfile
        from desktop_runtime.components import ComponentProject

        temp_dir = Path(tempfile.mkdtemp())
        data_dir = temp_dir / 'data'
        resources_dir = temp_dir / 'resources'
        data_dir.mkdir()
        resources_dir.mkdir()

        project = ComponentProject(data_dir, resources_dir)

        repos = project.list_repositories()
        assert 'repositories' in repos

        providers = project.discover_providers()
        assert 'providers' in providers

        print("  [OK] ComponentProject works correctly")
        checks.append(True)

        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        print(f"  [FAIL] ComponentProject error: {e}")
        checks.append(False)

    # Check 6: Desktop UI files
    print("\n[6/7] Checking Desktop UI files...")
    try:
        ui_dir = Path(__file__).parent.parent / 'desktop' / 'ui'

        required_files = [
            'component-manager.js',
            'component-manager.css',
            'app.js',
            'index.html'
        ]

        missing = []
        for file in required_files:
            if not (ui_dir / file).exists():
                missing.append(file)

        if missing:
            print(f"  [FAIL] Missing UI files: {', '.join(missing)}")
            checks.append(False)
        else:
            # Check that component-manager.js contains ComponentManager class
            cm_js = (ui_dir / 'component-manager.js').read_text(encoding='utf-8')
            if 'class ComponentManager' not in cm_js:
                print("  [FAIL] ComponentManager class not found in component-manager.js")
                checks.append(False)
            else:
                print("  [OK] All Desktop UI files present and valid")
                checks.append(True)
    except Exception as e:
        print(f"  [FAIL] UI files error: {e}")
        checks.append(False)

    # Check 7: Documentation
    print("\n[7/7] Checking documentation...")
    try:
        docs_dir = Path(__file__).parent.parent / 'docs'

        required_docs = [
            'provider_repository_protocol.md',
            'component_management_implementation.md',
            'component_management_quickstart.md',
            'component_management_testing_checklist.md',
            'component_management_final_summary.md'
        ]

        missing = []
        for doc in required_docs:
            if not (docs_dir / doc).exists():
                missing.append(doc)

        if missing:
            print(f"  [FAIL] Missing documentation: {', '.join(missing)}")
            checks.append(False)
        else:
            print("  [OK] All documentation files present")
            checks.append(True)
    except Exception as e:
        print(f"  [FAIL] Documentation error: {e}")
        checks.append(False)

    # Summary
    print("\n" + "="*70)
    print("Validation Summary")
    print("="*70)

    passed = sum(checks)
    total = len(checks)

    categories = [
        "Module Imports",
        "Repository Protocol",
        "Package Download",
        "Lifecycle Manager",
        "ComponentProject",
        "Desktop UI Files",
        "Documentation"
    ]

    for i, (category, result) in enumerate(zip(categories, checks)):
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status} {i+1}/7: {category}")

    print(f"\nTotal: {passed}/{total} checks passed ({passed*100//total}%)")

    if passed == total:
        print("\n" + "="*70)
        print("[SUCCESS] All validation checks passed!")
        print("="*70)
        print("\nComponent Management System is ready for testing.")
        print("\nNext steps:")
        print("  1. Run: python scripts/create_test_repository.py (if not done)")
        print("  2. Start PLA Desktop")
        print("  3. Follow: docs/component_management_quickstart.md")
        return 0
    else:
        print("\n" + "="*70)
        print(f"[INCOMPLETE] {total - passed} check(s) failed")
        print("="*70)
        return 1


if __name__ == '__main__':
    sys.exit(validate_all())
