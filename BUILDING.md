# Building Desktop Downloads

These steps are for maintainers. End users download an app from GitHub Releases
and do not need Python, pip, or a clone of the repository.

## Build on Your Computer

Use Python 3.12. From the project folder, create a build environment:

**Windows:**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe build_desktop.py
```

**macOS / Linux:**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python build_desktop.py
```

On Linux, Playwright may also require system libraries; follow its installation
instructions or use the GitHub Actions build below. The build always targets
the OS and CPU it runs on. PyInstaller does not cross-compile Windows apps on
a Mac or vice versa.

The build produces:

- `dist/`: the runnable app, including its Python runtime and libraries.
- `release/`: the downloadable ZIP or TAR.GZ and a SHA-256 checksum file.

The app is tested in a temporary working folder with Python environment settings
removed. A headless browser must render the QA Intelligence Agent, verify output
selection and right-side test count, load the sample, and select Quit successfully
before an archive is created. Desktop/mobile screenshots are saved under `build/`.
No API key, model
download, or live AI call is used. This does not verify actual model quality.
Playwright and its browser are build tools, not part of the end-user download.

Only explicitly selected application resources are packaged. Real `.env` files,
logs, the developer environment, and downloaded AI models are not bundled.
Never add them to the PyInstaller spec or commit `build/`, `dist/`, or `release/`.
These folders are ignored. Keep the final local archives and checksums for
testing, then upload them as GitHub Release assets instead of Git files.

## Build All Platforms on GitHub

1. Push the project to your GitHub repository.
2. Open **Actions > Build Desktop Downloads > Run workflow**.
3. Optionally enable **Create a draft GitHub Release** and enter a new version
   tag such as `v0.2.0`. Leave it unchecked for build artifacts only.
4. Wait for the Windows x64, Linux x64, Mac Intel, and Mac Apple Silicon jobs.
5. Download the build artifacts from the workflow run. They contain the app
   archives and checksums; extract the artifact wrapper before sharing them.

If the draft-release option was selected, all four successful platform builds
are attached to the draft. Review its assets and notes in **Releases**, then
publish the draft yourself. Alternatively, pushing a version tag such as
`v0.2.0` triggers all builds and a draft release. Use a new tag for a new release;
do not reuse a version that has already been published.

Each build uploads only its own OS/CPU archive and checksum, so unrelated
archives cannot be uploaded under another platform or overwrite the fresh
build when artifacts are merged. These downloads are built on
their respective native OS runners, not cross-compiled on your computer.

The workflow does not publish a public release automatically or commit generated
artifacts back to the repository. GitHub Actions usage may consume
billable minutes depending on your repository visibility and account plan.

## What Users Can Download

GitHub's **Releases** page and the repository's `release/` directory are different:

- The ignored local `release/` folder contains your build output. It does not
  create a GitHub Release or automatically upload downloads.
- Keep binaries out of Git. Push the workflow and source, then run the native
  platform builds to create and attach their downloads to a draft release.
- Workflow artifacts are available from the Actions run after a build succeeds.
- Release assets become publicly visible after the draft release is published.
- Source support or a configured matrix does not mean every binary has already
  been built or tested. Currently only the Mac Apple Silicon binary was built
  and browser-checked locally; verify the other jobs before announcing them.

## Before Public Distribution

- These are portable app archives, not Windows MSI installers or macOS DMGs.
  Windows/Linux users must keep the executable and bundled files together.
- Builds are currently unsigned; macOS builds use PyInstaller's ad-hoc signing,
  not Developer ID signing or notarization. Public distribution should add
  appropriate Windows code signing and Apple signing/notarization. Certificates
  are not included or requested by the project.
- Builds run a loopback-only browser UI; they are not native mobile apps.
- Local AI still needs a separately installed, running Ollama service. Users
  follow the sidebar setup and select Download AI model once Ollama is running.
- Desktop settings and logs are stored in the user's home folder under
  `.ai-testcase-generator/`, not inside the application bundle. Source runs
  continue to use the project's `.env` file. This legacy settings-folder name
  is retained so app renaming does not discard an existing user's configuration.
- App names are **QA Intelligence Agent.app** on macOS and **QAIntelligenceAgent**
  (`.exe` on Windows). Download archives use **QA-Intelligence-Agent**.
- Before widening support beyond the supplied runners, test on each target OS,
  CPU architecture, and minimum supported system version. Current macOS build
  metadata is version 0.2.0; update it in `desktop.spec` for future releases.