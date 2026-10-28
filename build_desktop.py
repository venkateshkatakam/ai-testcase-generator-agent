"""Build, smoke-test, and archive a desktop download for the current OS."""

import argparse
import hashlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parent


def platform_label() -> str:
    """Native dependencies require separate artifacts for each OS and CPU."""
    systems = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
    architectures = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}
    system = systems.get(platform.system())
    architecture = architectures.get(platform.machine().lower())
    if system is None or architecture is None:
        raise RuntimeError("This OS or CPU is not supported by the desktop build.")
    return f"{system}-{architecture}"


def bundle_paths(root: Path = ROOT) -> tuple[Path, Path]:
    """Locate the app and executable produced by the native PyInstaller build."""
    if platform.system() == "Darwin":
        bundle = root / "dist" / "QA Intelligence Agent.app"
        return bundle, bundle / "Contents" / "MacOS" / "QAIntelligenceAgent"
    bundle = root / "dist" / "QAIntelligenceAgent"
    executable = "QAIntelligenceAgent.exe" if platform.system() == "Windows" else "QAIntelligenceAgent"
    return bundle, bundle / executable


def check_browser(url: str, process: subprocess.Popen) -> None:
    """Render the real UI: a health response alone misses dynamic import errors."""
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.get_by_role("heading", name="QA Intelligence Agent", exact=True).wait_for(timeout=30_000)
            for label in ("Test Cases", "Regression Impact", "Playwright Scripts", "REST Assured Tests"):
                expect(page.get_by_text(label, exact=True)).to_be_visible()
            expect(page.get_by_text("Automation Candidates", exact=True)).to_have_count(0)
            expect(page.get_by_text("Quick start", exact=True)).to_be_visible()
            expect(page.get_by_role("checkbox", name="Generate fresh", exact=True)).to_be_visible()
            expect(page.get_by_text("Kafka", exact=True)).to_have_count(0)
            requirement_box = page.get_by_role("textbox", name="Requirement or user story").bounding_box()
            count_box = page.get_by_role("spinbutton", name="Tests per category").bounding_box()
            if requirement_box is None or count_box is None or count_box["x"] < requirement_box["x"] + requirement_box["width"]:
                raise RuntimeError("The tests-per-category control is not to the right of the requirement.")
            page.get_by_role("button", name="Quit app", exact=True).wait_for()
            page.get_by_role("button", name="Load sample", exact=True).click()
            page.wait_for_function("document.querySelector('textarea')?.value.includes('registered customer')")
            local_state = page.get_by_role("link", name="Install Ollama", exact=True).or_(
                page.get_by_role("button", name="Download AI model", exact=True)
            ).or_(page.get_by_text("Local AI is ready", exact=True))
            expect(local_state).to_be_visible()
            expect(page.get_by_role("button", name="Check connection", exact=True)).to_have_count(0)
            if page.get_by_role("link", name="Install Ollama", exact=True).is_visible():
                expect(page.get_by_role("button", name="Download AI model", exact=True)).to_have_count(0)
                expect(page.get_by_role("button", name="Generate selected outputs", exact=True)).to_be_disabled()
            page.get_by_text("OpenAI (GPT-4o)", exact=True).click()
            page.get_by_text("Test Cases", exact=True).click()
            expect(page.get_by_role("button", name="Generate selected outputs", exact=True)).to_be_disabled()
            page.get_by_text("Regression Impact", exact=True).click()
            expect(page.get_by_role("button", name="Generate selected outputs", exact=True)).to_be_enabled()
            page.get_by_text("Test Cases", exact=True).click()
            page.get_by_text("Regression Impact", exact=True).click()
            page.get_by_role("button", name="Validate API key", exact=True).wait_for()
            page.get_by_role("link", name="OpenAI billing", exact=True).wait_for()
            key_field = page.get_by_role("textbox", name="OpenAI API key", exact=True)
            expect(key_field).to_have_attribute("type", "password")
            key_field.fill("build-check-not-a-real-key")
            page.get_by_role("button", name="Clear API key", exact=True).click()
            expect(key_field).to_have_value("")
            screenshots = ROOT / "build"
            screenshots.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshots / "qa-intelligence-desktop.png"), full_page=True)
            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            mobile.goto(url)
            mobile.get_by_role("heading", name="QA Intelligence Agent", exact=True).wait_for(timeout=30_000)
            mobile.get_by_role("spinbutton", name="Tests per category").wait_for()
            if not mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"):
                raise RuntimeError("The mobile UI overflows horizontally.")
            mobile.screenshot(path=str(screenshots / "qa-intelligence-mobile.png"), full_page=True)
            mobile.close()
            page.get_by_text("Local AI (Ollama)", exact=True).click()
            page.get_by_role("button", name="Quit app", exact=True).click()
            process.wait(timeout=15)
        except Exception as error:
            raise RuntimeError("The packaged UI failed its browser check: " + str(error)) from error
        finally:
            browser.close()


def smoke_test(executable: Path) -> None:
    """Start the binary outside the source tree with no Python environment."""
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        environment = os.environ.copy()
        for variable in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
            environment.pop(variable, None)
        environment.update({"HOME": directory, "USERPROFILE": directory, "OPENAI_API_KEY": ""})
        environment["PATH"] = os.defpath
        opener = build_opener(ProxyHandler({}))
        with (temporary / "startup.log").open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                [str(executable.resolve()), "--serve", "--port", str(port)],
                cwd=temporary, env=environment, stdout=output, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 120
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                            healthy = response.status == 200 and response.read().strip() == b"ok"
                        if healthy:
                            with opener.open(f"http://127.0.0.1:{port}/", timeout=3) as page:
                                if page.status == 200 and b"streamlit" in page.read().lower():
                                    check_browser(f"http://127.0.0.1:{port}", process)
                                    try:
                                        exit_code = process.wait(timeout=15)
                                    except subprocess.TimeoutExpired as error:
                                        raise RuntimeError("The packaged app did not stop after selecting Quit app.") from error
                                    if exit_code != 0:
                                        raise RuntimeError(f"The packaged app exited unexpectedly: {exit_code}")
                                    print("Packaged app starts, renders its UI, and quits without system Python or the source folder.")
                                    return
                    except (URLError, OSError):
                        pass
                    Event().wait(0.25)
                logs = []
                for path in (temporary / "startup.log", temporary / ".ai-testcase-generator" / "app.log"):
                    if path.exists():
                        logs.append(path.read_text(encoding="utf-8", errors="replace")[-4000:])
                raise RuntimeError("Packaged app failed its startup check.\n" + "\n".join(logs))
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def archive_bundle(root: Path = ROOT) -> Path:
    """Archive only build output, preserving macOS links and Linux permissions."""
    bundle, executable = bundle_paths(root)
    if not executable.is_file():
        raise RuntimeError("Desktop executable is missing. Build the app first.")
    release = root / "release"
    release.mkdir(exist_ok=True)
    base = release / f"QA-Intelligence-Agent-{platform_label()}"
    if platform.system() == "Darwin":
        archive = base.with_suffix(".zip")
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(archive)], check=True)
    else:
        archive_format = "zip" if platform.system() == "Windows" else "gztar"
        archive = Path(shutil.make_archive(str(base), archive_format, root_dir=bundle.parent, base_dir=bundle.name))
    with archive.open("rb") as content:
        checksum = hashlib.file_digest(content, "sha256").hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    return archive


def main(argv: list[str] | None = None) -> int:
    """Build on the OS being targeted; PyInstaller does not cross-compile."""
    parser = argparse.ArgumentParser(description="Build a Python-free desktop download for this machine's OS.")
    parser.add_argument("--package-only", action="store_true", help="Test and archive an existing dist build")
    args = parser.parse_args(argv)
    try:
        platform_label()
        if not args.package_only:
            subprocess.run([
                sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--log-level", "WARN",
                str(ROOT / "desktop.spec"),
            ], cwd=ROOT, check=True)
        _, executable = bundle_paths()
        smoke_test(executable)
        archive = archive_bundle()
        print(f"Desktop download: {archive}")
        print(f"Archive size: {archive.stat().st_size / (1024 * 1024):.1f} MB")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())