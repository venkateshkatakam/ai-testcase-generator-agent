# QA Intelligence Agent

Turn a user story or requirement into test cases and QA reports using AI.
Choose only the outputs you need and download the results.

**Outputs:** Test Cases, Regression Impact, Playwright Scripts, REST Assured Tests.

Runs on **Windows, macOS, and Linux**. Generated scripts are drafts to review,
not tests automatically executed against your application.

## Use the App (No Python Needed)

1. Open this repository's **Releases** section and download the app for your
   computer. If no release has been published, use the source setup below.
2. Extract the download and open the app:

| Your computer | Download ending | Open after extracting |
| --- | --- | --- |
| Windows (Intel/AMD 64-bit) | `windows-x64.zip` | `QAIntelligenceAgent.exe` |
| Mac (Apple Silicon) | `macos-arm64.zip` | `QA Intelligence Agent.app` |
| Mac (Intel) | `macos-x64.zip` | `QA Intelligence Agent.app` |
| Linux (Intel/AMD 64-bit) | `linux-x64.tar.gz` | `QAIntelligenceAgent` |

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

For building and publishing app downloads, see [BUILDING.md](BUILDING.md).
Commit source code and documentation, not `.env`, `.venv`, `build/`, `dist/`,
or `release/`. Publish app downloads as **GitHub Release assets**, not Git files.