# PyInstaller build for Readout:  pyinstaller readout.spec
# Produces dist/Readout/Readout.exe (one-folder build: starts faster than one-file).

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ["main.py"],
    datas=collect_data_files("customtkinter") + [("assets/readout.ico", "assets")],
    # pyttsx3 picks its driver at runtime and comtypes generates SAPI bindings lazily
    hiddenimports=collect_submodules("pyttsx3.drivers") + collect_submodules("comtypes"),
    excludes=["pytest", "unittest", "pydoc"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Readout",
    icon="assets/readout.ico",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Readout")
