from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = collect_all('yt_dlp')
ejs_data, ejs_binaries, ejs_imports = collect_all('yt_dlp_ejs')
datas += ejs_data + [
    (str(root / 'build/vendor/tools'), 'tools'),
    (str(root / 'build/vendor/runtime-licenses'), 'licenses'),
    (str(root / 'assets/app.ico'), 'assets'),
    (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
]
binaries += ejs_binaries
hiddenimports += ejs_imports
a = Analysis([str(root / 'app.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='YouTube Downloader',
          console=False, upx=False, icon=str(root / 'assets/app.ico'))
coll = COLLECT(exe, a.binaries, a.datas, name='YouTube Downloader', upx=False)
