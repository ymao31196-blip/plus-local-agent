"""Pre-deployment checklist and automated verification.

This script performs comprehensive checks before deployment to ensure
the component management system is ready for production use.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


def print_section(title):
    """Print a section header."""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)


def check_code_files():
    """Verify all code files are present and valid."""
    print_section("CODE FILES")

    files = {
        'Core Modules': [
            'src/provider/provider_repository.py',
            'src/provider/provider_lifecycle.py',
        ],
        'Desktop Runtime': [
            'src/desktop_runtime/components.py',
            'src/desktop_runtime/manager.py',
        ],
        'UI Components': [
            'desktop/ui/component-manager.js',
            'desktop/ui/component-manager.css',
            'desktop/ui/app.js',
            'desktop/ui/index.html',
        ],
        'Tests': [
            'tests/test_component_integration.py',
            'tests/test_component_rpc.py',
        ],
        'Tools': [
            'scripts/create_test_repository.py',
            'scripts/validate_component_system.py',
        ]
    }

    all_ok = True
    for category, file_list in files.items():
        print(f"\n{category}:")
        for file_path in file_list:
            path = Path(__file__).parent.parent / file_path
            if path.exists():
                size = path.stat().st_size
                print(f"  [OK] {file_path} ({size:,} bytes)")
            else:
                print(f"  [FAIL] {file_path} - MISSING")
                all_ok = False

    return all_ok


def check_documentation():
    """Verify all documentation is present."""
    print_section("DOCUMENTATION")

    docs = [
        'docs/provider_repository_protocol.md',
        'docs/component_management_implementation.md',
        'docs/component_management_quickstart.md',
        'docs/component_management_testing_checklist.md',
        'docs/component_management_final_summary.md',
        'docs/deployment_readiness_report.md',
        'docs/RELEASE_NOTES_v1.0.0.md',
        'docs/PROJECT_DELIVERY_SUMMARY.md',
        'COMPONENT_MANAGEMENT_README.md',
    ]

    all_ok = True
    for doc in docs:
        path = Path(__file__).parent.parent / doc
        if path.exists():
            size = path.stat().st_size
            lines = len(path.read_text(encoding='utf-8').splitlines())
            print(f"  [OK] {doc} ({lines} lines)")
        else:
            print(f"  [FAIL] {doc} - MISSING")
            all_ok = False

    return all_ok


def check_test_repository():
    """Verify test repository is complete."""
    print_section("TEST REPOSITORY")

    base = Path(__file__).parent.parent / 'test-provider-repository'

    required_files = [
        'index.json',
        'README.md',
        'providers/test-echo-provider/metadata.json',
        'providers/test-echo-provider/versions/index.json',
        'providers/test-echo-provider/versions/1.0.0/manifest.json',
        'providers/test-echo-provider/versions/1.0.0/spec.json',
        'providers/test-echo-provider/versions/1.0.0/checksums.json',
        'providers/test-echo-provider/versions/1.0.0/assets/providers/test_echo_provider.py',
    ]

    if not base.exists():
        print("  [FAIL] Test repository not found")
        print("  Run: python scripts/create_test_repository.py")
        return False

    all_ok = True
    for file_path in required_files:
        path = base / file_path
        if path.exists():
            print(f"  [OK] {file_path}")
        else:
            print(f"  [FAIL] {file_path} - MISSING")
            all_ok = False

    return all_ok


def check_imports():
    """Verify all Python modules can be imported."""
    print_section("MODULE IMPORTS")

    imports = [
        ('provider.provider_repository', 'RepositoryClient'),
        ('provider.provider_lifecycle', 'ProviderLifecycleManager'),
        ('desktop_runtime.components', 'ComponentProject'),
    ]

    all_ok = True
    for module_name, class_name in imports:
        try:
            module = __import__(module_name, fromlist=[class_name])
            getattr(module, class_name)
            print(f"  [OK] {module_name}.{class_name}")
        except Exception as e:
            print(f"  [FAIL] {module_name}.{class_name} - {e}")
            all_ok = False

    return all_ok


def run_unit_tests():
    """Run automated tests."""
    print_section("AUTOMATED TESTS")

    import subprocess

    tests = [
        ('Integration Tests', 'tests/test_component_integration.py'),
        ('System Validation', 'scripts/validate_component_system.py'),
    ]

    all_ok = True
    for name, test_file in tests:
        print(f"\n{name} ({test_file}):")
        try:
            result = subprocess.run(
                [sys.executable, test_file],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                # Count passed tests
                output = result.stdout
                if 'passed' in output.lower():
                    print(f"  [OK] Tests passed")
                else:
                    print(f"  [OK] Completed successfully")
            else:
                print(f"  [FAIL] Tests failed (exit code {result.returncode})")
                all_ok = False

        except subprocess.TimeoutExpired:
            print(f"  [FAIL] Tests timed out")
            all_ok = False
        except Exception as e:
            print(f"  [FAIL] Error running tests: {e}")
            all_ok = False

    return all_ok


def check_code_quality():
    """Check code quality metrics."""
    print_section("CODE QUALITY")

    metrics = {
        'Total Files': 0,
        'Total Lines': 0,
        'Python Files': 0,
        'JavaScript Files': 0,
        'Documentation Files': 0,
    }

    base = Path(__file__).parent.parent

    # Count files
    for pattern, key in [
        ('src/**/*.py', 'Python Files'),
        ('desktop/ui/**/*.js', 'JavaScript Files'),
        ('docs/**/*.md', 'Documentation Files'),
    ]:
        files = list(base.glob(pattern))
        metrics[key] = len(files)
        metrics['Total Files'] += len(files)

        for file in files:
            try:
                lines = len(file.read_text(encoding='utf-8').splitlines())
                metrics['Total Lines'] += lines
            except:
                pass

    print("\nCode Statistics:")
    for key, value in metrics.items():
        print(f"  {key}: {value:,}")

    # Check for TODO/FIXME
    print("\nCode Health:")
    todo_count = 0
    fixme_count = 0

    for file in base.glob('src/**/*.py'):
        try:
            content = file.read_text(encoding='utf-8')
            todo_count += content.count('TODO')
            fixme_count += content.count('FIXME')
        except:
            pass

    print(f"  TODO markers: {todo_count}")
    print(f"  FIXME markers: {fixme_count}")

    if todo_count > 10 or fixme_count > 5:
        print("  [WARN] High number of TODO/FIXME markers")
        return False

    print("  [OK] Code health acceptable")
    return True


def check_git_status():
    """Check git repository status."""
    print_section("GIT STATUS")

    import subprocess

    try:
        # Check if in git repo
        result = subprocess.run(
            ['git', 'rev-parse', '--git-dir'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            print("  [SKIP] Not in a git repository")
            return True

        # Check for uncommitted changes
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.stdout.strip():
            print("  [WARN] Uncommitted changes detected")
            print("  Consider committing before deployment")
        else:
            print("  [OK] Working directory clean")

        # Get current branch
        result = subprocess.run(
            ['git', 'branch', '--show-current'],
            capture_output=True,
            text=True,
            timeout=5
        )

        branch = result.stdout.strip()
        print(f"  Current branch: {branch}")

        return True

    except Exception as e:
        print(f"  [SKIP] Git check failed: {e}")
        return True


def check_deployment_requirements():
    """Check deployment prerequisites."""
    print_section("DEPLOYMENT REQUIREMENTS")

    checks = [
        ('Python 3.11+', sys.version_info >= (3, 11)),
        ('pathlib available', True),
        ('json available', True),
        ('hashlib available', True),
    ]

    all_ok = True
    for name, passed in checks:
        status = "[OK]" if passed else "[FAIL]"
        print(f"  {status} {name}")
        if not passed:
            all_ok = False

    # Check for optional tools
    print("\nOptional Tools:")
    import shutil

    optional = [
        ('uv', 'Environment management'),
        ('git', 'Version control'),
    ]

    for tool, purpose in optional:
        found = shutil.which(tool) is not None
        status = "[OK]" if found else "[WARN]"
        print(f"  {status} {tool} - {purpose}")

    return all_ok


def generate_report():
    """Generate deployment readiness report."""
    print_section("DEPLOYMENT READINESS REPORT")

    checks = [
        ('Code Files', check_code_files),
        ('Documentation', check_documentation),
        ('Test Repository', check_test_repository),
        ('Module Imports', check_imports),
        ('Automated Tests', run_unit_tests),
        ('Code Quality', check_code_quality),
        ('Git Status', check_git_status),
        ('Deployment Requirements', check_deployment_requirements),
    ]

    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n[ERROR] {name} check failed: {e}")
            results.append((name, False))

    # Summary
    print_section("SUMMARY")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    print(f"\nChecks Passed: {passed}/{total}")
    print("\nDetailed Results:")

    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} {name}")

    # Final verdict
    print("\n" + "="*70)

    if passed == total:
        print("  STATUS: SOURCE PREFLIGHT PASSED")
        print("="*70)
        print("\nSource preflight checks passed. Frozen Runtime, actual NSIS installation and native UI acceptance remain separate required checks.")
        print("\nNext Steps:")
        print("  1. Review deployment plan")
        print("  2. Prepare deployment package")
        print("  3. Run packaged MCP and isolated NSIS/native UI acceptance")
        print("  4. Record actual results before distributing the candidate")
        return 0
    else:
        print("  STATUS: NOT READY - ISSUES FOUND")
        print("="*70)
        print(f"\n{total - passed} check(s) failed.")
        print("\nPlease address the issues above before deployment.")
        return 1


if __name__ == '__main__':
    print("="*70)
    print("  Component Management System - Pre-Deployment Check")
    print("="*70)

    sys.exit(generate_report())
