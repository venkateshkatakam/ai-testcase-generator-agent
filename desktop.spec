# Build with: python -m PyInstaller --clean --noconfirm desktop.spec
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


root = Path(SPECPATH)
data_files = collect_data_files("streamlit") + [
    (str(root / "ui" / "streamlit_app.py"), "ui"),
    (str(root / "sample_input.txt"), "."),
    (str(root / ".env.example"), "."),
]
hidden_imports = [
    "main", "agents.analyzer", "agents.testcase_generator", "agents.formatter",
    "streamlit.runtime.scriptrunner.magic_funcs",
]
for package in ("langgraph", "langchain_core", "langchain_openai", "langchain_ollama", "tiktoken_ext"):
    hidden_imports += collect_submodules(package)
for distribution in ("streamlit", "langgraph", "langchain-core", "langchain-openai", "langchain-ollama"):
    data_files += copy_metadata(distribution, recursive=True)

analysis = Analysis(
    [str(root / "launch.py")], pathex=[str(root)],
    binaries=[], datas=data_files, hiddenimports=hidden_imports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "IPython", "notebook", "tkinter", "streamlit.testing"],
    noarchive=False,
)
python_archive = PYZ(analysis.pure)
executable = EXE(
    python_archive, analysis.scripts, [], exclude_binaries=True,
    name="QAIntelligenceAgent", debug=False, strip=False, upx=False,
    console=True,
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="QAIntelligenceAgent",
)
if sys.platform == "darwin":
    application = BUNDLE(
        collection, name="QA Intelligence Agent.app",
        bundle_identifier="com.qaintelligenceagent.desktop",
        info_plist={"CFBundleShortVersionString": "0.2.0", "CFBundleVersion": "2"},
    )