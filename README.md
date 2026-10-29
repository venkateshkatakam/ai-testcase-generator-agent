# QA Intelligence Agent

Turn a user story or requirement into test cases and QA reports using AI.
Choose only the outputs you need and download the results.

**Outputs:** Test Cases, Regression Impact, Playwright Scripts, REST Assured Tests.

Available for **Windows, macOS, and Linux**. Generated
scripts are drafts to review, not tests automatically executed against your application.

## Available Downloads

**v0.2.0 is published.** All four platform builds completed successfully in
GitHub Actions. Download the app for your computer from the
[latest release](https://github.com/venkateshkatakam/ai-testcase-generator-agent/releases/latest):

| Your computer | Download | Open after extracting |
| --- | --- | --- |
| Windows (Intel/AMD 64-bit) | [Windows ZIP](https://github.com/venkateshkatakam/ai-testcase-generator-agent/releases/latest/download/QA-Intelligence-Agent-windows-x64.zip) | `QAIntelligenceAgent.exe` |
| Mac (Apple Silicon) | [Apple Silicon ZIP](https://github.com/venkateshkatakam/ai-testcase-generator-agent/releases/latest/download/QA-Intelligence-Agent-macos-arm64.zip) | `QA Intelligence Agent.app` |
| Mac (Intel) | [Intel Mac ZIP](https://github.com/venkateshkatakam/ai-testcase-generator-agent/releases/latest/download/QA-Intelligence-Agent-macos-x64.zip) | `QA Intelligence Agent.app` |
| Linux (Intel/AMD 64-bit) | [Linux TAR.GZ](https://github.com/venkateshkatakam/ai-testcase-generator-agent/releases/latest/download/QA-Intelligence-Agent-linux-x64.tar.gz) | `QAIntelligenceAgent` |

Published means the app is available to download and launch. Before generating
results, set up **Ollama and a model** or an **OpenAI API key** as described below.
Download an app archive, not GitHub's **Source code** archives. SHA-256 checksum
files are also available under the release's **Assets** section.

GitHub **Releases** is separate from the local `release/` folder. App archives
and checksums are uploaded as Release assets, not committed to source control.

## Use the App (No Python Needed)

1. Download the app for your computer using the table above.
2. Extract the download and open the app listed in the last column.
3. The app opens in your browser. Choose an AI option below.
4. Paste a requirement or select **Load sample**.
5. Select your **Required outputs**, set **Tests per category**, then select
   **Generate selected outputs**.
6. Review the result tabs and download what you need. Select **Quit app** to stop.

Keep extracted files together. Python is bundled, but Ollama and its AI model
are not. Current builds are unsigned, so your OS may warn or block them. Only
open downloads you trust. See [BUILDING.md](BUILDING.md) for platform limitations.

## Choose an AI Option

### Local AI: No API Key

1. Install [Ollama](https://ollama.com/download) and open it.
2. In the app, select **Local AI (Ollama)** and follow the sidebar setup.
3. Select **Continue setup**, then **Download AI model** when prompted.
4. Once **Local AI is ready** appears, generate your outputs.

The model download is several GB. Around **16 GB RAM and 6 GB free disk space**
are recommended for the default model. Keep Ollama running. After setup, local
generation can work offline without API payments.

### OpenAI: Optional Paid API

1. Create an [OpenAI API key](https://platform.openai.com/api-keys) and add API credits.
2. Select **OpenAI (GPT-4o)** in the app.
3. Paste your key into **OpenAI API key** and select **Validate API key**.

The entered key is masked, session-only, and not saved to disk. Use **Clear API
key** to remove it. Validation checks access, not available credits. API billing
is separate from a ChatGPT subscription. Cloud mode sends requirements to OpenAI;
do not submit secrets or sensitive customer data.

## Run from Source

For developers or users without a published app download. Install
[Python 3.11+](https://www.python.org/downloads/) (3.12 recommended), clone the
repository, and open a terminal in the project folder.

**Windows:**

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe launch.py
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python launch.py
```

No `.env` file is needed for local defaults or UI key entry. Developers can
optionally use [.env.example](.env.example) for environment-based configuration.

## Tips and Common Issues

| Issue | What to do |
| --- | --- |
| Ollama is unavailable | Open Ollama, then select **Continue setup**. |
| Model is missing | Select **Download AI model** in the sidebar. |
| Generation is slow | Start with one test per category and fewer outputs. First local runs can take a few minutes. |
| You want a new answer | Select **Generate fresh**. Otherwise, matching results are reused within your session. |
| Coverage still fails after repair | Increase the test count or split the requirement into smaller stories. |
| OpenAI returns quota/rate errors | Check **OpenAI billing**, credits, and project limits. |
| Browser does not open | Use the local address printed in the terminal. |

## Tests and Releases

Tests do not need an API key or a local model:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Use the first command on Windows and the second on macOS/Linux. GitHub Actions
is configured to run the tests on all three platforms.

To publish downloads:

1. Commit and push the source code, documentation, and workflow changes.
2. Open **Actions > Build Desktop Downloads > Run workflow** on GitHub.
3. Enable **Create a draft GitHub Release**, enter an unused tag such as
   `v0.2.1`, and start the workflow. Do not reuse the published `v0.2.0` tag.
4. Wait for all four platform builds to pass.
5. Open **Releases**, review the draft's app archives and checksums, and publish it.

See [BUILDING.md](BUILDING.md) for details. Keep `.env`, `.venv`, `build/`, `dist/`,
and `release/` out of Git. Ignoring local downloads does not prevent them from
being uploaded as Release assets, and does not delete your local copies.