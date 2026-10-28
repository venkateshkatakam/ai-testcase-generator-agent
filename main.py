"""LangGraph orchestration and command-line entry point."""

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

import openai
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from ollama import Client as OllamaClient

from agents.analyzer import RequirementAnalysis, analyze_requirement
from agents.formatter import format_output, rows_to_csv
from agents.qa_intelligence import (
    OUTPUT_OPTIONS, QA_STAGES, RegressionImpact, ScriptDraft, generate_qa_stage,
)
from agents.testcase_generator import TestCase, TestSuite, TestSuiteValidationError, generate_test_cases, validate_suite


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent
MAX_REQUIREMENT_LENGTH = 20_000
DEFAULT_LOCAL_MODEL = "qwen2.5:7b"


class AgentState(TypedDict, total=False):
    """Shared graph state; each agent adds its own output."""

    requirement: str
    tests_per_category: int
    analysis: RequirementAnalysis
    test_cases: list[TestCase]
    rows: list[dict[str, str]]
    table: str
    regression_impact: RegressionImpact
    playwright: ScriptDraft
    rest_assured: ScriptDraft


class GenerationError(RuntimeError):
    """A sanitized, actionable error suitable for CLI and UI users."""


class LocalModel:
    """Adapt Ollama's native schema output to the agents' shared model interface."""

    def __init__(self, model: ChatOllama):
        self.model = model

    def with_structured_output(self, schema: Any, *, method: Literal["json_schema"], strict: bool) -> Any:
        # Ollama constrains decoding with a schema; strict is an OpenAI-only flag.
        return self.model.with_structured_output(schema, method=method)


def configure_logging() -> None:
    """Log workflow metadata, never requirements, model responses, or secrets."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore", "openai", "langchain", "langgraph"):
        logging.getLogger(name).setLevel(logging.WARNING)


def configuration_path() -> Path:
    """Keep desktop settings writable and outside the installed application."""
    if getattr(sys, "frozen", False):
        return Path.home() / ".ai-testcase-generator" / ".env"
    return PROJECT_ROOT / ".env"


def local_base_url() -> str:
    """Restrict Local AI to loopback addresses on every supported OS."""
    address = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlsplit(address)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path):
        raise GenerationError("OLLAMA_BASE_URL must be a local HTTP address such as http://127.0.0.1:11434.")
    return address


def get_local_models() -> list[str]:
    """Inspect installed local models without downloading or sending story text."""
    load_dotenv(configuration_path(), override=False)
    address = local_base_url() + "/api/tags"
    try:
        with build_opener(ProxyHandler({})).open(address, timeout=3) as response:
            payload = json.load(response)
        return [model["name"] for model in payload["models"]
                if isinstance(model["name"], str)
                and not model.get("remote_host") and not model.get("remote_model")
                and not model["name"].endswith((":cloud", "-cloud"))]
    except (URLError, OSError, ValueError, KeyError, TypeError):
        raise GenerationError("Ollama is not available. Install and start Ollama, then check the connection again.") from None


def pull_local_model(model_name: str) -> Iterator[dict[str, Any]]:
    """Download a model only after an explicit user action in the UI."""
    load_dotenv(configuration_path(), override=False)
    model_name = model_name.strip()
    if not model_name or model_name.endswith((":cloud", "-cloud")):
        raise GenerationError("Choose a downloadable local model such as qwen2.5:7b.")
    address = local_base_url()
    try:
        with OllamaClient(host=address, timeout=600, trust_env=False) as client:
            for progress in client.pull(model_name, stream=True):
                yield progress.model_dump()
    except Exception as error:
        LOGGER.error("Model download failed (%s)", type(error).__name__)
        raise GenerationError("Model download failed. Check that Ollama is running, your internet connection, and free disk space.") from None


def openai_configuration(api_key: str | None = None) -> tuple[str, str]:
    """Resolve credentials consistently for validation and generation."""
    load_dotenv(configuration_path(), override=False)
    key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
    if not key or key == "your_openai_api_key_here":
        raise GenerationError("Enter an OpenAI API key in the sidebar, or set OPENAI_API_KEY in your environment or settings file.")
    return key, os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"


def safe_error_message(error: Exception) -> str:
    """Map known failures to fixed messages without echoing provider responses."""
    if isinstance(error, TestSuiteValidationError):
        return error.user_message
    if isinstance(error, openai.AuthenticationError):
        return "OpenAI rejected the API key. Replace it with a valid OpenAI Platform API key, then validate again."
    if isinstance(error, openai.RateLimitError):
        if error.code in {"insufficient_quota", "billing_hard_limit_reached"}:
            return (
                "OpenAI API credits are exhausted or the project's spending limit has been reached. "
                "Check OpenAI billing and project limits. A ChatGPT subscription does not include API credits."
            )
        return (
            "OpenAI returned a rate-limit error (HTTP 429). Wait briefly and retry. "
            "If it continues, check API credits, project spending limits, and request/token limits."
        )
    if isinstance(error, (openai.PermissionDeniedError, openai.NotFoundError)):
        return "The OpenAI project cannot access the selected model or API resource. Check model permissions and project access."
    if isinstance(error, openai.APITimeoutError):
        return "OpenAI timed out. Check your connection and try a shorter requirement or fewer test cases."
    if isinstance(error, openai.APIConnectionError):
        return "Cannot connect to OpenAI. Check your internet connection, VPN, proxy, and firewall."
    if isinstance(error, openai.BadRequestError):
        return "OpenAI rejected the request. Check that the selected model supports structured output, or shorten the requirement."
    if isinstance(error, openai.APIStatusError):
        return "OpenAI returned a service error. Try again later and check OpenAI service status."
    return (
        "The AI response could not be completed or validated. Try fewer tests or a smaller story. "
        "For Local AI, also check that Ollama is running and the model is installed."
    )


def validate_openai_key(api_key: str | None = None) -> str:
    """Check authentication/model visibility without sending a generation request."""
    key, model_name = openai_configuration(api_key)
    try:
        with openai.OpenAI(api_key=key, timeout=15, max_retries=0) as client:
            client.models.retrieve(model_name)
        return model_name
    except Exception as error:
        LOGGER.error("OpenAI validation failed (%s)", type(error).__name__)
        raise GenerationError(safe_error_message(error)) from None


def create_model(
    provider: str | None = None, local_model: str | None = None, *, api_key: str | None = None,
) -> ChatOpenAI | LocalModel:
    """Use key-free local inference by default; cloud access is always opt-in."""
    load_dotenv(configuration_path(), override=False)
    provider = (provider or os.getenv("AI_PROVIDER", "ollama")).strip().lower()
    if provider == "ollama":
        model_name = (local_model or os.getenv("OLLAMA_MODEL", DEFAULT_LOCAL_MODEL)).strip() or DEFAULT_LOCAL_MODEL
        if model_name not in get_local_models():
            raise GenerationError(f"Local model '{model_name}' is not installed. Run: ollama pull {model_name}")
        return LocalModel(ChatOllama(
            model=model_name, base_url=local_base_url(), temperature=0,
            num_ctx=16_384, num_predict=8_000,
            keep_alive="15m",
            client_kwargs={"timeout": 300, "trust_env": False},
        ))
    if provider != "openai":
        raise GenerationError("Choose a supported AI provider: ollama or openai.")
    api_key, model_name = openai_configuration(api_key)
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        temperature=0,
        timeout=90,
        max_retries=2,
        max_tokens=12_000,
    )


def selected_outputs(outputs: Sequence[str] | None, include_intelligence: bool = False) -> tuple[str, ...]:
    """Validate requested deliverables and run them in a stable dependency order."""
    if outputs is None:
        outputs = list(OUTPUT_OPTIONS) if include_intelligence else ["test_cases"]
    if isinstance(outputs, (str, bytes)) or not outputs or any(output not in OUTPUT_OPTIONS for output in outputs):
        raise GenerationError("Choose at least one supported output.")
    return tuple(output for output in OUTPUT_OPTIONS if output in outputs)


def build_graph(model: Any, *, outputs: Sequence[str] | None = None, include_intelligence: bool = False) -> Any:
    """Generate supporting test cases, then only the requested QA deliverables."""
    requested = selected_outputs(outputs, include_intelligence)
    workflow = StateGraph(AgentState)

    def analyzer_node(state: AgentState) -> dict[str, Any]:
        if "analysis" in state:
            LOGGER.info("Reusing validated requirement analysis")
            return {"analysis": state["analysis"]}
        LOGGER.info("Running Requirement Analyzer Agent")
        return analyze_requirement(dict(state), model)

    def generator_node(state: AgentState) -> dict[str, Any]:
        if "test_cases" in state:
            LOGGER.info("Reusing validated test cases")
            return {"test_cases": state["test_cases"]}
        LOGGER.info("Running Test Case Generator Agent")
        return generate_test_cases(dict(state), model)

    def formatter_node(state: AgentState) -> dict[str, Any]:
        LOGGER.info("Running Output Formatter Agent")
        return format_output(state)

    workflow.add_node("requirement_analyzer", analyzer_node)
    workflow.add_node("test_case_generator", generator_node)
    workflow.add_node("output_formatter", formatter_node)
    workflow.add_edge(START, "requirement_analyzer")
    workflow.add_edge("requirement_analyzer", "test_case_generator")
    workflow.add_edge("test_case_generator", "output_formatter")
    previous = "output_formatter"
    if any(stage in requested for stage, _ in QA_STAGES):
        def intelligence_node(state: AgentState, *, stage: str) -> dict[str, Any]:
            if stage in state:
                LOGGER.info("Reusing QA output: %s", stage)
                return {stage: state[stage]}
            LOGGER.info("Running QA stage: %s", stage)
            return generate_qa_stage(dict(state), model, stage)

        for stage, _ in QA_STAGES:
            if stage not in requested:
                continue
            workflow.add_node(stage, partial(intelligence_node, stage=stage))
            workflow.add_edge(previous, stage)
            previous = stage
    workflow.add_edge(previous, END)
    return workflow.compile()


def reusable_context(requirement: str, count: int, reuse: AgentState | None) -> AgentState:
    """Reuse only matching session context; revalidate cached tests before use."""
    state: AgentState = {"requirement": requirement, "tests_per_category": count}
    if not reuse or reuse.get("requirement") != requirement or reuse.get("tests_per_category") != count:
        return state
    analysis = reuse.get("analysis")
    if not isinstance(analysis, RequirementAnalysis):
        return state
    state["analysis"] = analysis
    if "test_cases" not in reuse:
        return state
    try:
        suite = TestSuite(test_cases=reuse["test_cases"])
        validate_suite(suite, dict(state))
    except ValueError:
        return state
    state["test_cases"] = suite.test_cases
    state.update(format_output(state))
    for stage, _ in QA_STAGES:
        if stage in reuse:
            state[stage] = reuse[stage]
    return state


def generate_test_suite(
    requirement: str, tests_per_category: int = 3, *, model: Any = None,
    provider: str | None = None, local_model: str | None = None, api_key: str | None = None,
    include_intelligence: bool = False,
    outputs: Sequence[str] | None = None,
    on_stage: Callable[[str, dict[str, Any]], None] | None = None,
    reuse: AgentState | None = None,
) -> AgentState:
    """Validate input and run the complete workflow; allow offline model injection."""
    if not isinstance(requirement, str) or not requirement.strip():
        raise GenerationError("Enter a non-empty requirement or user story.")
    requirement = requirement.strip()
    if len(requirement) > MAX_REQUIREMENT_LENGTH:
        raise GenerationError(f"Requirements must be at most {MAX_REQUIREMENT_LENGTH:,} characters.")
    if type(tests_per_category) is not int or not 1 <= tests_per_category <= 5:
        raise GenerationError("Tests per category must be an integer from 1 to 5.")
    requested = selected_outputs(outputs, include_intelligence)

    started = time.monotonic()
    try:
        result = reusable_context(requirement, tests_per_category, reuse)
        if "test_cases" in result and all(output == "test_cases" or output in result for output in requested):
            LOGGER.info("Reused session outputs in %.2fs", time.monotonic() - started)
            if on_stage is not None:
                on_stage("session_cache", dict(result))
            return result
        graph = build_graph(
            model if model is not None else create_model(provider, local_model, api_key=api_key),
            outputs=requested,
        )
        for update in graph.stream(result, stream_mode="updates"):
            for stage, values in update.items():
                result.update(values)
                if on_stage is not None:
                    on_stage(stage, dict(result))
        LOGGER.info("Generated %d test cases in %.2fs", len(result["rows"]), time.monotonic() - started)
        return result
    except GenerationError:
        raise
    except Exception as error:
        # Provider exception text can include request content; do not log it.
        LOGGER.error("Generation failed (%s)", type(error).__name__)
        raise GenerationError(safe_error_message(error)) from None


def cli() -> int:
    """Accept inline text or a UTF-8 input file and print/export a table."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Generate AI-assisted test case tables.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Requirement or user story")
    source.add_argument("--file", type=Path, help="UTF-8 requirement file")
    parser.add_argument("--per-category", type=int, default=3, help="Tests per category (1-5)")
    parser.add_argument("--provider", choices=("ollama", "openai"), help="AI provider (default: AI_PROVIDER or ollama)")
    parser.add_argument("--local-model", help="Installed Ollama model override")
    parser.add_argument("--format", choices=("markdown", "csv"), default="markdown")
    parser.add_argument("--output", type=Path, help="Optional output file instead of stdout")
    args = parser.parse_args()
    try:
        requirement = args.file.read_text(encoding="utf-8") if args.file else args.text
        result = generate_test_suite(requirement, args.per_category, provider=args.provider, local_model=args.local_model)
        output = rows_to_csv(result["rows"]) if args.format == "csv" else result["table"]
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output)
        return 0
    except (GenerationError, OSError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())