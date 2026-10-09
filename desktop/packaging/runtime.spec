# Run from the repository root with the isolated packaging interpreter.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parents[1]
sys.path.insert(0, str(root / 'src'))
datas, binaries, hiddenimports = [], [], []
for package in ('fastmcp', 'mcp', 'mcp_types'):
    d, b, h = collect_all(package, filter_submodules=lambda name: '.cli' not in name)
    datas += d
    binaries += b
    hiddenimports += h
for package in ('desktop_runtime', 'execution', 'capabilities', 'routing', 'runtime',
                'hooks', 'transactions', 'project', 'artifacts', 'browser', 'agent',
                'provider', 'tooling', 'host', 'diagnostics', 'mcp_runtime'):
    hiddenimports += collect_submodules(package)
datas += copy_metadata('fastmcp') + copy_metadata('mcp')
a = Analysis([str(root / 'src/desktop_runtime/entry.py')], pathex=[str(root / 'src')],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], runtime_hooks=[], excludes=['pytest', 'IPython', 'numpy', 'pandas'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='pla-runtime',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='pla-runtime')
