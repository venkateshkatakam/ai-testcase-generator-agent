"""Streamlit interface for selectable QA intelligence outputs."""

import _thread
import os
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from streamlit.runtime import get_instance


# Streamlit starts from ui/; make the repository's modules importable as well.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.formatter import rows_to_csv
from agents.qa_intelligence import OUTPUT_OPTIONS, RegressionImpact, ScriptDraft
from agents.testcase_generator import CATEGORIES
from main import (
    DEFAULT_LOCAL_MODEL, GenerationError, MAX_REQUIREMENT_LENGTH,
    configuration_path, configure_logging, generate_test_suite, get_local_models, pull_local_model,
    validate_openai_key,
)


def clear_result() -> None:
    """Prevent a previous provider's results being attributed to a new selection."""
    st.session_state.pop("result", None)
    st.session_state.pop("result_outputs", None)
    st.session_state.pop("key_validation", None)
    st.session_state.pop("generation_cache", None)


def clear_generated_outputs() -> None:
    """Output selection changes must not invalidate an accepted API key."""
    st.session_state.pop("result", None)
    st.session_state.pop("result_outputs", None)


def clear_api_key() -> None:
    """Remove the user-entered credential without changing any shared settings."""
    st.session_state["openai_api_key"] = ""
    clear_result()


def request_app_shutdown() -> None:
    """Wake the server loop and let Streamlit's SIGINT handler stop it cleanly."""
    get_instance()._get_async_objs().eventloop.call_soon_threadsafe(_thread.interrupt_main)


def render_local_setup() -> tuple[str, bool]:
    """Show only the local setup action that can succeed in the current state."""
    with st.expander("Advanced settings"):
        model_name = st.text_input(
            "Local model", value=os.getenv("OLLAMA_MODEL", DEFAULT_LOCAL_MODEL),
            key="local_model", on_change=clear_result,
        ).strip() or DEFAULT_LOCAL_MODEL
    try:
        installed = get_local_models()
    except GenerationError:
        st.warning("Step 1 of 2: Ollama is not running")
        st.caption("Install Ollama once, open it, then return here. This app uses it to run AI on your computer.")
        st.link_button("Install Ollama", "https://ollama.com/download", icon=":material/open_in_new:")
        st.caption("Already installed? Open Ollama on your computer.")
        st.button("Continue setup", icon=":material/refresh:")
        return model_name, False

    ready = model_name in installed
    if not ready:
        st.info("Step 2 of 2: Download the AI model")
        st.caption("One-time download of several GB. Ollama is connected.")
        if st.button("Download AI model", icon=":material/download:"):
            progress_bar = st.progress(0, text="Starting download...")
            try:
                for progress in pull_local_model(model_name):
                    total = progress.get("total") or 0
                    completed = progress.get("completed") or 0
                    fraction = min(max(completed / total, 0), 1) if total else 0
                    progress_bar.progress(fraction, text=progress.get("status", "Downloading..."))
                ready = model_name in get_local_models()
                if ready:
                    st.rerun()
                else:
                    progress_bar.empty()
                    st.warning("The model is not available yet. Retry setup.")
            except GenerationError as error:
                progress_bar.empty()
                st.error(str(error))
        st.button("Refresh setup", icon=":material/refresh:")
    if ready:
        st.success("Local AI is ready")
    return model_name, ready


def render_test_table(result: dict[str, Any]) -> None:
    """Render the existing test table and exports when explicitly requested."""
    st.subheader(f"Test cases ({len(result['rows'])})")
    selected = st.multiselect("Categories", options=CATEGORIES, default=list(CATEGORIES))
    visible_rows = [row for row in result["rows"] if row["Category"] in selected]
    if visible_rows:
        st.dataframe(visible_rows, hide_index=True, width="stretch")
    else:
        st.info("No test cases match the selected categories.")
    csv_column, markdown_column = st.columns(2)
    csv_column.download_button(
        "Download CSV", data=rows_to_csv(result["rows"]), file_name="test_cases.csv",
        mime="text/csv", icon=":material/download:",
    )
    markdown_column.download_button(
        "Download Markdown", data=result["table"], file_name="test_cases.md",
        mime="text/markdown", icon=":material/download:",
    )


def render_qa_report(output: str, report: RegressionImpact | ScriptDraft) -> None:
    """Display requested recommendations or code without executing model output."""
    if isinstance(report, RegressionImpact):
        st.write(report.summary)
        st.dataframe([{
            "Area": area.area, "Risk": area.risk, "Reason": area.reason,
            "Test cases": ", ".join(area.test_case_ids), "Checks": "\n".join(area.checks),
            "Assumptions": "\n".join(area.assumptions) or "None",
        } for area in report.areas], hide_index=True, width="stretch")
    else:
        if report.status == "Not applicable":
            st.info(report.reason)
        else:
            st.warning("Unexecuted draft. Review assumptions and setup before running.")
            st.text(f"{report.status}: {report.reason}")
            language = "typescript" if output == "playwright" else "java"
            file_name = "qa-intelligence.spec.ts" if output == "playwright" else "QAIntelligenceApiTest.java"
            st.code(report.code, language=language)
            st.download_button(
                "Download script", data=report.code, file_name=file_name,
                mime="text/plain", key=f"script_{output}", icon=":material/download:",
            )
        if report.setup or report.assumptions:
            with st.expander("Setup and assumptions"):
                for item in report.setup:
                    st.text(item)
                for item in report.assumptions:
                    st.text(f"Assumption: {item}")
    st.download_button(
        "Download report", data=report.model_dump_json(indent=2), file_name=f"{output}.json",
        mime="application/json", key=f"report_{output}", icon=":material/download:",
    )


def render_app() -> None:
    """Keep the last successful result available across filter/download reruns."""
    configure_logging()
    load_dotenv(configuration_path(), override=False)
    st.set_page_config(page_title="QA Intelligence Agent", layout="wide")
    st.title("QA Intelligence Agent")
    with st.expander("Quick start", expanded="generation_cache" not in st.session_state):
        st.markdown(
            "1. Set up **Local AI** in the sidebar, or choose **OpenAI** and enter an API key.\n"
            "2. Paste a requirement with its rules and expected behavior, or select **Load sample**.\n"
            "3. Select the outputs you need, set the test count, and choose **Generate selected outputs**."
        )

    with st.sidebar:
        provider_label = st.radio(
            "AI provider", ("Local AI (Ollama)", "OpenAI (GPT-4o)"),
            key="provider", on_change=clear_result,
        )
        provider = "ollama" if provider_label == "Local AI (Ollama)" else "openai"
        local_model = None
        api_key = None
        ready = True
        if provider == "ollama":
            local_model, ready = render_local_setup()
        else:
            entered_key = st.text_input(
                "OpenAI API key", type="password", key="openai_api_key",
                placeholder="sk-...", on_change=clear_result,
                help="Used only for this browser session. Not saved to disk. Leave blank to use an environment or settings key.",
            )
            api_key = entered_key.strip() or None
            if st.button("Validate API key", icon=":material/verified_user:"):
                try:
                    with st.spinner("Checking OpenAI access..."):
                        model_name = validate_openai_key(api_key)
                    st.session_state["key_validation"] = {
                        "ok": True,
                        "message": f"Key accepted; {model_name} is available. API credits and generation quota are not checked.",
                    }
                except GenerationError as error:
                    st.session_state["key_validation"] = {"ok": False, "message": str(error)}
            status = st.session_state.get("key_validation")
            if status:
                if status["ok"]:
                    st.success(status["message"])
                else:
                    st.error(status["message"])
            st.button("Clear API key", icon=":material/key_off:", on_click=clear_api_key)
            st.link_button("Get API key", "https://platform.openai.com/api-keys", icon=":material/key:")
            st.link_button("OpenAI billing", "https://platform.openai.com/settings/organization/billing/overview", icon=":material/credit_card:")
        if st.button("Load sample", icon=":material/article:"):
            try:
                st.session_state["requirement"] = (PROJECT_ROOT / "sample_input.txt").read_text(encoding="utf-8")
            except OSError:
                st.error("The sample requirement file could not be read.")
        if getattr(sys, "frozen", False):
            if st.button("Quit app", icon=":material/power_settings_new:"):
                request_app_shutdown()

    requested_outputs = st.pills(
        "Required outputs", options=list(OUTPUT_OPTIONS), format_func=OUTPUT_OPTIONS.__getitem__,
        selection_mode="multi", default=["test_cases"], key="selected_outputs",
        on_change=clear_generated_outputs,
    )

    with st.form("requirement_form"):
        requirement_column, settings_column = st.columns([4, 1])
        with requirement_column:
            requirement = st.text_area(
                "Requirement or user story", key="requirement", height=240,
                max_chars=MAX_REQUIREMENT_LENGTH,
                placeholder="As a registered user, I want to reset my password...",
                help="Include acceptance criteria, limits, and API details when known. Clear, focused requirements produce better results.",
            )
        with settings_column:
            count = st.number_input(
                "Tests per category", min_value=1, max_value=5, value=1, step=1,
                help="1 produces 5 supporting tests: one in each category. Higher counts take longer.",
            )
            fresh = st.checkbox(
                "Generate fresh", value=False,
                help="Ignore this session's saved work. Leave unchecked to reuse matching analysis and tests and generate only missing outputs.",
            )
        submitted = st.form_submit_button(
            "Generate selected outputs", type="primary", icon=":material/auto_fix_high:",
            disabled=not ready or not requested_outputs,
        )

    if submitted:
        # Do not present an old result as the output of a failed new request.
        clear_generated_outputs()
        started = time.monotonic()
        progress = st.status("Generating selected outputs...", expanded=True)
        signature = (provider, local_model, os.getenv("OPENAI_MODEL", "gpt-4o"))
        cached = st.session_state.get("generation_cache")
        reuse = cached["state"] if not fresh and cached and cached["signature"] == signature else None

        def stage_complete(stage: str, state: dict[str, Any]) -> None:
            st.session_state["generation_cache"] = {"signature": signature, "state": state}
            label = {"requirement_analyzer": "Requirement analysis", "test_case_generator": "Supporting test cases", "output_formatter": "Table formatting", "session_cache": "Saved session results"}.get(stage, OUTPUT_OPTIONS.get(stage, stage))
            progress.write(f"Completed: {label}")

        try:
            result = generate_test_suite(
                requirement, int(count), provider=provider, local_model=local_model, api_key=api_key,
                outputs=requested_outputs, on_stage=stage_complete, reuse=reuse,
            )
            st.session_state["result"] = result
            st.session_state["result_outputs"] = tuple(output for output in OUTPUT_OPTIONS if output in requested_outputs)
            progress.update(label=f"Outputs ready ({time.monotonic() - started:.1f}s)", state="complete", expanded=False)
        except GenerationError as error:
            progress.update(label="Generation failed", state="error", expanded=False)
            st.error(str(error))

    result = st.session_state.get("result")
    if result is None:
        return

    st.divider()
    completed_outputs = st.session_state.get("result_outputs", ("test_cases",))
    tabs = st.tabs([OUTPUT_OPTIONS[output] for output in completed_outputs])
    for output, tab in zip(completed_outputs, tabs):
        with tab:
            if output == "test_cases":
                render_test_table(result)
            else:
                render_qa_report(output, result[output])

    with st.expander("Requirement analysis"):
        analysis = result["analysis"]
        st.write(analysis.summary)
        st.dataframe(
            [{"ID": criterion.id, "Acceptance criterion": criterion.description}
             for criterion in analysis.acceptance_criteria],
            hide_index=True, width="stretch",
        )
        for label, values in (
            ("Actors", analysis.actors), ("Business rules", analysis.business_rules),
            ("Boundaries", analysis.boundaries), ("API details", analysis.api_details),
            ("Regression areas", analysis.regression_areas),
            ("Assumptions", analysis.assumptions), ("Open questions", analysis.open_questions),
        ):
            st.text(label)
            for value in values:
                st.text(f"- {value}")
            if not values:
                st.text("None identified")
        st.text("Submitted requirement")
        st.text(result["requirement"])


if __name__ == "__main__":
    render_app()