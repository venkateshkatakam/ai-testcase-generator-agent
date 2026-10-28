"""Offline workflow, export, CLI, and Streamlit checks; no API key is needed."""

import contextlib
import csv
import io
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import openai
from langchain_ollama import ChatOllama
from streamlit.proto.TextInput_pb2 import TextInput
from streamlit.testing.v1 import AppTest

from agents.analyzer import RequirementAnalysis
from agents.formatter import COLUMNS, format_output, markdown_cell, rows_to_csv
from agents.qa_intelligence import OUTPUT_OPTIONS, RegressionImpact, ScriptDraft
from agents.testcase_generator import CATEGORIES, TestCase, TestSuite
from build_desktop import archive_bundle, bundle_paths, platform_label
from launch import available_port, main as launch_app, project_python, server_command, stop_server, wait_for_server
from main import GenerationError, LocalModel, cli, configuration_path, create_model, generate_test_suite, get_local_models, pull_local_model, validate_openai_key


ROOT = Path(__file__).resolve().parents[1]


def sample_analysis() -> RequirementAnalysis:
    """Minimal valid analysis fixture shared by all entry-point checks."""
    return RequirementAnalysis(
        summary="Reset a password", actors=["Customer"],
        acceptance_criteria=[{"id": "AC-001", "description": "Reset a password securely"}],
        business_rules=[], boundaries=[], api_details=[], regression_areas=[],
        assumptions=[], open_questions=["What is the password policy?"],
    )


def sample_suite() -> TestSuite:
    """One unique test for each of the five mandatory categories."""
    return TestSuite(test_cases=[
        TestCase(
            category=category, title=f"{category} password reset", priority="High",
            requirement_refs=["AC-001"], preconditions=["A customer exists"],
            steps=["Submit a password reset request", "Inspect the response"],
            test_data="customer@example.com", expected_result="The specified outcome is observed",
            assumptions=["Confirm the unspecified password policy before execution"],
        ) for category in CATEGORIES
    ])


def fake_model(analysis=None, suite=None) -> Mock:
    """Mock only the model boundary; execute the real LangGraph and validators."""
    model = Mock()
    structured = Mock()
    structured.invoke.side_effect = itertools.chain([
        sample_analysis() if analysis is None else analysis,
    ], itertools.repeat(sample_suite() if suite is None else suite))
    model.with_structured_output.return_value = structured
    return model


def sample_reports():
    """Linked reports for testing complete and independently selected outputs."""
    return {
        "regression_impact": RegressionImpact(summary="Login impact", areas=[{
            "area": "Login", "risk": "High", "reason": "Password changes affect login",
            "test_case_ids": ["TC-001"], "checks": ["Verify the new password works"], "assumptions": [],
        }]),
        "playwright": ScriptDraft(
            status="Needs clarification", reason="Selectors are not specified", test_case_ids=["TC-001"],
            code="import { test } from '@playwright/test';\ntest.skip('TODO: supply selectors', async () => {});",
            setup=["Install @playwright/test"], assumptions=["Confirm the UI selectors"],
        ),
        "rest_assured": ScriptDraft(
            status="Needs clarification", reason="Response contract needs confirmation", test_case_ids=["TC-004"],
            code='@org.junit.jupiter.api.Disabled("TODO: confirm response contract")\nclass QAIntelligenceApiTest {}',
            setup=["Configure JUnit 5 and REST Assured"], assumptions=["Confirm the API contract"],
        ),
    }


class WorkflowTests(unittest.TestCase):
    """Exercise the orchestrator and schema guardrails without network calls."""

    def test_graph_runs_all_three_agents(self):
        model = fake_model()
        result = generate_test_suite("  Reset my password  ", 1, model=model)
        self.assertEqual(result["requirement"], "Reset my password")
        self.assertEqual(len(result["rows"]), 5)
        self.assertEqual({row["Category"] for row in result["rows"]}, set(CATEGORIES))
        self.assertEqual(result["rows"][0]["ID"], "TC-001")
        self.assertEqual(result["rows"][-1]["ID"], "TC-005")
        self.assertEqual(model.with_structured_output.call_count, 2)
        self.assertIn("| Expected Result |", result["table"])
        self.assertEqual(len(result["table"].splitlines()), 7)

    def test_repeat_request_reuses_validated_context_without_model_calls(self):
        original = generate_test_suite("Story", 1, model=fake_model())
        unused = Mock()
        with patch("main.create_model") as factory:
            result = generate_test_suite("Story", 1, model=unused, reuse=original)
        self.assertEqual(result["rows"], original["rows"])
        unused.with_structured_output.assert_not_called()
        factory.assert_not_called()

    def test_added_output_reuses_base_and_generates_only_missing_stage(self):
        original = generate_test_suite("Story", 1, model=fake_model())
        report = sample_reports()["regression_impact"]
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [report]
        result = generate_test_suite("Story", 1, model=model, outputs=["regression_impact"], reuse=original)
        self.assertEqual(result["regression_impact"], report)
        self.assertEqual(result["test_cases"], original["test_cases"])
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 1)

    def test_changed_requirement_does_not_reuse_prior_tests(self):
        original = generate_test_suite("Story", 1, model=fake_model())
        model = fake_model()
        result = generate_test_suite("Different story", 1, model=model, reuse=original)
        self.assertEqual(result["requirement"], "Different story")
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)

    def test_changed_count_does_not_reuse_prior_tests(self):
        original = generate_test_suite("Story", 1, model=fake_model())
        suite = sample_suite()
        suite.test_cases += [case.model_copy(update={"title": case.title + " alternate"}) for case in suite.test_cases]
        model = fake_model(suite=suite)
        result = generate_test_suite("Story", 2, model=model, reuse=original)
        self.assertEqual(len(result["rows"]), 10)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)

    def test_invalid_cached_tests_are_not_accepted(self):
        original = generate_test_suite("Story", 1, model=fake_model())
        original["test_cases"][0].requirement_refs = ["AC-unknown"]
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [sample_suite()]
        result = generate_test_suite("Story", 1, model=model, reuse=original)
        self.assertEqual(result["test_cases"][0].requirement_refs, ["AC-001"])
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 1)

    def test_invalid_inputs_never_call_model(self):
        model = fake_model()
        for requirement, count in [("", 1), ("  ", 1), ("a" * 20_001, 1), ("Story", 0), ("Story", 6), ("Story", True)]:
            with self.subTest(count=count, length=len(requirement)), self.assertRaises(GenerationError):
                generate_test_suite(requirement, count, model=model)
        model.with_structured_output.assert_not_called()

    def test_only_selected_regression_report_runs(self):
        report = RegressionImpact(summary="Password reset impact", areas=[{
            "area": "Login", "risk": "High", "reason": "Password changes affect login",
            "test_case_ids": ["TC-001"], "checks": ["Verify the new password works"], "assumptions": [],
        }])
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [sample_analysis(), sample_suite(), report]
        updates = []
        result = generate_test_suite("Story", 1, model=model, outputs=["regression_impact"], on_stage=lambda name, state: updates.append(name))
        self.assertEqual(result["regression_impact"], report)
        self.assertNotIn("automation_plan", result)
        self.assertNotIn("playwright", result)
        self.assertNotIn("rest_assured", result)
        self.assertEqual(model.with_structured_output.call_count, 3)
        self.assertEqual(updates[-1], "regression_impact")

    def test_script_can_run_without_unrequested_reports(self):
        for stage in ("playwright", "rest_assured"):
            draft = ScriptDraft(
                status="Needs clarification", reason="Application details are missing",
                test_case_ids=["TC-001"], code="// TODO: supply the application contract", setup=[], assumptions=[],
            )
            model = fake_model()
            model.with_structured_output.return_value.invoke.side_effect = [sample_analysis(), sample_suite(), draft]
            with self.subTest(stage=stage):
                result = generate_test_suite("Story", 1, model=model, outputs=[stage])
                self.assertEqual(result[stage], draft)
                self.assertNotIn("automation_plan", result)
                self.assertNotIn("regression_impact", result)
                self.assertEqual(model.with_structured_output.call_count, 3)

    def test_invalid_or_empty_outputs_never_call_model(self):
        for outputs in ([], ["kafka_validation"], ["automation_plan"], ["unknown"], "playwright"):
            model = fake_model()
            with self.subTest(outputs=outputs), self.assertRaises(GenerationError):
                generate_test_suite("Story", 1, model=model, outputs=outputs)
            model.with_structured_output.assert_not_called()
        self.assertFalse(any("kafka" in option for option in OUTPUT_OPTIONS))

    def test_unselected_automation_plan_is_not_required_for_non_applicable_script(self):
        draft = ScriptDraft(status="Not applicable", reason="No browser interaction", test_case_ids=[], code="", setup=[], assumptions=[])
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [sample_analysis(), sample_suite(), draft]
        result = generate_test_suite("Story", 1, model=model, outputs=["playwright"])
        self.assertEqual(result["playwright"].status, "Not applicable")

    def test_all_selected_reports_run_once_in_dependency_order(self):
        reports = sample_reports()
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [sample_analysis(), sample_suite(), *reports.values()]
        stages = []
        result = generate_test_suite(
            "Story", 1, model=model, outputs=[*reversed(OUTPUT_OPTIONS), "playwright"],
            on_stage=lambda name, state: stages.append(name),
        )
        self.assertEqual(stages[-3:], list(reports))
        self.assertEqual(model.with_structured_output.call_count, 5)
        for stage, report in reports.items():
            self.assertEqual(result[stage], report)
        self.assertNotIn("kafka_validation", result)

    def test_script_cannot_reference_unknown_or_duplicate_tests(self):
        reports = sample_reports()
        for test_ids in (["TC-999"], ["TC-001", "TC-001"]):
            draft = reports["playwright"].model_copy(update={"test_case_ids": test_ids})
            model = fake_model()
            model.with_structured_output.return_value.invoke.side_effect = [
                sample_analysis(), sample_suite(), draft,
            ]
            with self.subTest(test_ids=test_ids), self.assertRaises(GenerationError):
                generate_test_suite("Story", 1, model=model, outputs=["playwright"])

    def test_missing_category_is_rejected(self):
        suite = sample_suite()
        suite.test_cases[-1] = suite.test_cases[0].model_copy(update={"title": "Another functional test"})
        with self.assertRaises(GenerationError):
            generate_test_suite("Story", 1, model=fake_model(suite=suite))

    def test_wrong_category_count_is_rejected(self):
        with self.assertRaises(GenerationError):
            generate_test_suite("Story", 2, model=fake_model())

    def test_incomplete_coverage_is_repaired_with_exact_feedback(self):
        analysis = sample_analysis()
        analysis.acceptance_criteria.append(
            analysis.acceptance_criteria[0].model_copy(update={"id": "AC-002", "description": "Reject expired reset links"})
        )
        repaired = {"criterion_1": {
            "preconditions": ["An expired reset link exists"],
            "steps": ["Submit the expired password reset link"],
            "test_data": "An expired token", "expected_result": "The expired link is rejected",
            "assumptions": [],
        }}
        model = fake_model()
        structured = model.with_structured_output.return_value
        structured.invoke.side_effect = [analysis, sample_suite(), repaired]
        result = generate_test_suite("Story", 1, model=model)
        self.assertEqual(len(result["rows"]), 5)
        self.assertIn("AC-002: Submit the expired password reset link", result["test_cases"][0].steps)
        self.assertIn("AC-002: The expired link is rejected", result["test_cases"][0].expected_result)
        feedback = json.loads(structured.invoke.call_args.args[0][-1].content)["validation_feedback"]
        self.assertEqual(feedback["missing_criteria"], ["AC-002"])
        self.assertEqual(structured.invoke.call_count, 3)

    def test_persistent_validation_failure_stops_after_one_repair(self):
        model = fake_model()
        with self.assertRaisesRegex(GenerationError, "after one repair attempt"):
            generate_test_suite("Story", 2, model=model)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 3)

    def test_incomplete_coverage_patch_is_not_accepted(self):
        analysis = sample_analysis()
        for criterion_id in ("AC-002", "AC-003"):
            analysis.acceptance_criteria.append(
                analysis.acceptance_criteria[0].model_copy(update={"id": criterion_id})
            )
        partial_repair = {"criterion_1": {
            "preconditions": [], "steps": ["Perform the required check"], "test_data": "Sample data",
            "expected_result": "The requirement is verified", "assumptions": [],
        }}
        model = fake_model()
        structured = model.with_structured_output.return_value
        structured.invoke.side_effect = [analysis, sample_suite(), partial_repair]
        with self.assertRaises(GenerationError):
            generate_test_suite("Story", 1, model=model)
        self.assertEqual(structured.invoke.call_count, 3)

    def test_duplicate_titles_can_be_repaired_without_accepting_bad_data(self):
        duplicate = sample_suite()
        duplicate.test_cases[-1].title = duplicate.test_cases[0].title
        model = fake_model()
        structured = model.with_structured_output.return_value
        structured.invoke.side_effect = [sample_analysis(), duplicate, sample_suite()]
        self.assertEqual(len(generate_test_suite("Story", 1, model=model)["rows"]), 5)
        feedback = json.loads(structured.invoke.call_args.args[0][-1].content)["validation_feedback"]
        self.assertTrue(feedback["duplicate_titles"])

    def test_unknown_and_missing_references_are_rejected(self):
        for references in (["AC-999"], ["AC-001"]):
            analysis = sample_analysis()
            analysis.acceptance_criteria.append(
                analysis.acceptance_criteria[0].model_copy(update={"id": "AC-002"})
            )
            suite = sample_suite()
            for case in suite.test_cases:
                case.requirement_refs = references
            with self.subTest(references=references), self.assertRaises(GenerationError):
                generate_test_suite("Story", 1, model=fake_model(analysis=analysis, suite=suite))

    def test_duplicate_criteria_are_rejected(self):
        analysis = sample_analysis()
        analysis.acceptance_criteria.append(analysis.acceptance_criteria[0])
        with self.assertRaises(GenerationError):
            generate_test_suite("Story", 1, model=fake_model(analysis=analysis))

    def test_duplicate_titles_are_rejected(self):
        suite = sample_suite()
        suite.test_cases[-1].title = suite.test_cases[0].title.upper()
        with self.assertRaises(GenerationError):
            generate_test_suite("Story", 1, model=fake_model(suite=suite))

    def test_provider_failure_does_not_leak_secrets(self):
        model = fake_model()
        model.with_structured_output.side_effect = RuntimeError("sk-secret private requirement")
        with self.assertLogs("main", level="ERROR") as captured:
            with self.assertRaises(GenerationError) as error:
                generate_test_suite("Private story", model=model)
        self.assertNotIn("sk-secret", str(error.exception))
        self.assertNotIn("private requirement", " ".join(captured.output))
        self.assertIn("RuntimeError", " ".join(captured.output))

    def test_invalid_structured_response_is_rejected(self):
        with self.assertRaises(GenerationError):
            generate_test_suite("Story", 1, model=fake_model(analysis={"summary": "Incomplete"}))

    def test_missing_api_key_has_actionable_error(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(GenerationError, "OPENAI_API_KEY"):
                create_model("openai")

    def test_default_model_is_gpt4o(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {"OPENAI_API_KEY": "test", "OPENAI_MODEL": ""}):
            with patch("main.ChatOpenAI") as constructor:
                create_model("openai")
                self.assertEqual(constructor.call_args.kwargs["model"], "gpt-4o")

    def test_session_key_overrides_config_without_changing_environment(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {"OPENAI_API_KEY": "configured-key"}):
            with patch("main.ChatOpenAI") as constructor:
                create_model("openai", api_key="  session-test-key  ")
                self.assertEqual(constructor.call_args.kwargs["api_key"], "session-test-key")
                import os

                self.assertEqual(os.environ["OPENAI_API_KEY"], "configured-key")

    def test_session_key_is_not_in_graph_state_or_logs(self):
        with patch("main.create_model", return_value=fake_model()) as factory:
            with self.assertLogs("main", level="INFO") as captured:
                result = generate_test_suite("Story", 1, provider="openai", api_key="session-test-key")
        factory.assert_called_once_with("openai", None, api_key="session-test-key")
        self.assertNotIn("session-test-key", repr(result))
        self.assertNotIn("session-test-key", " ".join(captured.output))
        self.assertNotIn("api_key", result)

    def test_key_validation_checks_model_without_generation(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {"OPENAI_MODEL": "gpt-4o"}):
            with patch("main.openai.OpenAI") as constructor, patch("main.ChatOpenAI") as generation:
                self.assertEqual(validate_openai_key("session-test-key"), "gpt-4o")
                constructor.assert_called_once_with(api_key="session-test-key", timeout=15, max_retries=0)
                constructor.return_value.__enter__.return_value.models.retrieve.assert_called_once_with("gpt-4o")
                generation.assert_not_called()

    def test_key_validation_rejects_empty_key_before_network(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with patch("main.openai.OpenAI") as client, self.assertRaises(GenerationError):
                validate_openai_key("")
            client.assert_not_called()

    def test_openai_failures_have_safe_specific_messages(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        for error_class, status, code, expected in [
            (openai.AuthenticationError, 401, "invalid_api_key", "rejected the API key"),
            (openai.RateLimitError, 429, "insufficient_quota", "API credits are exhausted"),
            (openai.RateLimitError, 429, "rate_limit_exceeded", "rate-limit error"),
            (openai.PermissionDeniedError, 403, "permission_denied", "cannot access"),
            (openai.NotFoundError, 404, "model_not_found", "cannot access"),
            (openai.BadRequestError, 400, "invalid_request", "rejected the request"),
            (openai.InternalServerError, 500, "server_error", "service error"),
        ]:
            error = error_class(
                "private-key and private requirement", response=httpx.Response(status, request=request),
                body={"code": code, "message": "private-key and private requirement"},
            )
            model = fake_model()
            model.with_structured_output.side_effect = error
            with self.subTest(error=error_class.__name__, code=code):
                with self.assertLogs("main", level="ERROR") as captured:
                    with self.assertRaisesRegex(GenerationError, expected) as caught:
                        generate_test_suite("Story", 1, model=model)
                self.assertNotIn("private-key", str(caught.exception))
                self.assertNotIn("private requirement", " ".join(captured.output))

    def test_key_validation_sanitizes_provider_failures(self):
        request = httpx.Request("GET", "https://api.openai.com/v1/models/gpt-4o")
        error = openai.AuthenticationError(
            "secret-key", response=httpx.Response(401, request=request), body={"code": "invalid_api_key"},
        )
        with patch("main.load_dotenv"), patch("main.openai.OpenAI") as client:
            client.return_value.__enter__.return_value.models.retrieve.side_effect = error
            with self.assertRaisesRegex(GenerationError, "rejected the API key") as caught:
                validate_openai_key("session-test-key")
            self.assertNotIn("secret-key", str(caught.exception))

    def test_local_is_default_and_needs_no_openai_key(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            with patch("main.get_local_models", return_value=["qwen2.5:7b"]), patch("main.ChatOllama") as constructor:
                create_model()
                self.assertEqual(constructor.call_args.kwargs["model"], "qwen2.5:7b")
                self.assertEqual(constructor.call_args.kwargs["base_url"], "http://127.0.0.1:11434")
                self.assertNotIn("api_key", constructor.call_args.kwargs)

    def test_missing_local_model_has_setup_command(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            with patch("main.get_local_models", return_value=[]):
                with self.assertRaisesRegex(GenerationError, "ollama pull qwen2.5:7b"):
                    create_model("ollama")

    def test_desktop_settings_are_outside_bundle(self):
        with patch("main.sys.frozen", True, create=True), patch("main.Path.home", return_value=Path("user-home")):
            self.assertEqual(configuration_path(), Path("user-home") / ".ai-testcase-generator" / ".env")

    def test_explicit_model_download_streams_progress(self):
        event = Mock()
        event.model_dump.return_value = {"status": "success"}
        with patch("main.load_dotenv"), patch("main.OllamaClient") as constructor:
            constructor.return_value.__enter__.return_value.pull.return_value = iter([event])
            self.assertEqual(list(pull_local_model("qwen2.5:7b")), [{"status": "success"}])
            constructor.return_value.__enter__.return_value.pull.assert_called_once_with("qwen2.5:7b", stream=True)

    def test_cloud_model_download_is_rejected(self):
        with patch("main.load_dotenv"), self.assertRaises(GenerationError):
            list(pull_local_model("large:cloud"))

    def test_provider_selection_reaches_factory(self):
        with patch("main.create_model", return_value=fake_model()) as factory:
            generate_test_suite("Story", 1, provider="openai")
        factory.assert_called_once_with("openai", None, api_key=None)

    def test_remote_local_endpoint_is_rejected(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {"OLLAMA_BASE_URL": "https://example.com"}):
            with self.assertRaisesRegex(GenerationError, "local HTTP"):
                create_model("ollama")

    def test_native_ollama_structured_output_runs_real_graph(self):
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            schema_name = payload["format"]["title"]
            output = sample_analysis() if schema_name == "RequirementAnalysis" else sample_suite()
            return httpx.Response(200, json={
                "model": "qwen2.5:7b", "message": {"role": "assistant", "content": output.model_dump_json()},
                "done": True, "done_reason": "stop",
            })

        model = LocalModel(ChatOllama(
            model="qwen2.5:7b", num_ctx=16_384,
            client_kwargs={"transport": httpx.MockTransport(respond)},
        ))
        result = generate_test_suite("Reset my password", 1, model=model)
        self.assertEqual(len(result["rows"]), 5)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["options"]["num_ctx"], 16_384)

    def test_unavailable_ollama_has_actionable_error(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            with patch("main.build_opener") as opener:
                opener.return_value.open.side_effect = OSError("private server detail")
                with self.assertRaisesRegex(GenerationError, "Install and start Ollama"):
                    get_local_models()

    def test_cloud_models_are_not_offered_as_offline(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            with patch("main.build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.read.return_value = json.dumps({"models": [
                    {"name": "qwen2.5:7b"}, {"name": "large:cloud"},
                    {"name": "remote-alias", "remote_host": "https://example.com"},
                ]}).encode()
                self.assertEqual(get_local_models(), ["qwen2.5:7b"])

    def test_formatter_escapes_cells_and_numbers_steps(self):
        suite = sample_suite()
        suite.test_cases[0].title = "Title | with\nnewline"
        result = format_output({"test_cases": suite.test_cases})
        self.assertIn("Title \\| with<br>newline", result["table"])
        self.assertTrue(result["rows"][0]["Steps"].startswith("1. "))
        self.assertEqual(markdown_cell("left\\right\r\nnext"), "left\\\\right<br>next")

    def test_csv_quotes_and_protects_formulas(self):
        for value in ('=HYPERLINK("https://example.com")', "  +1", "-1", "@SUM(1)", "\t=1"):
            row = {column: value for column in COLUMNS}
            parsed = list(csv.DictReader(io.StringIO(rows_to_csv([row]))))
            self.assertEqual(parsed[0]["Title"], "'" + value)
        row = {column: "comma, pipe| and\nnewline" for column in COLUMNS}
        self.assertEqual(list(csv.DictReader(io.StringIO(rows_to_csv([row]))))[0], row)

    def test_cli_writes_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "story.txt"
            destination = Path(directory) / "cases.csv"
            source.write_text("Reset my password", encoding="utf-8")
            arguments = ["main.py", "--file", str(source), "--per-category", "1", "--format", "csv", "--output", str(destination)]
            with patch("sys.argv", arguments), patch("main.create_model", return_value=fake_model()):
                self.assertEqual(cli(), 0)
            self.assertEqual(len(list(csv.DictReader(io.StringIO(destination.read_text())))), 5)

    def test_cli_reports_missing_file(self):
        with patch("sys.argv", ["main.py", "--file", "/nonexistent/story.txt"]):
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(cli(), 1)
            self.assertIn("Error:", output.getvalue())


class StreamlitTests(unittest.TestCase):
    """Exercise widget submission, error feedback, and retained table results."""

    def setUp(self):
        local_models = patch("main.get_local_models", return_value=["qwen2.5:7b"])
        local_models.start()
        self.addCleanup(local_models.stop)

    def test_desktop_quit_is_scheduled_on_server_loop(self):
        from ui.streamlit_app import request_app_shutdown

        with patch("ui.streamlit_app.get_instance") as runtime:
            request_app_shutdown()
            runtime.return_value._get_async_objs.return_value.eventloop.call_soon_threadsafe.assert_called_once()

    def app(self):
        return AppTest.from_file(str(ROOT / "ui" / "streamlit_app.py"), default_timeout=20).run()

    def submit(self, app):
        next(button for button in app.button if button.label == "Generate selected outputs").click().run()

    def test_selectable_outputs_have_no_kafka_and_require_a_selection(self):
        app = self.app()
        self.assertEqual(app.title[0].value, "QA Intelligence Agent")
        self.assertEqual(app.pills[0].value, ["test_cases"])
        self.assertFalse(any("Kafka" in option for option in app.pills[0].options))
        self.assertFalse(any("Automation" in option for option in app.pills[0].options))
        app.pills[0].set_value([]).run()
        self.assertTrue(next(button for button in app.button if button.label == "Generate selected outputs").disabled)

    def test_ui_reuses_results_and_generate_fresh_bypasses_reuse(self):
        app = self.app()
        app.text_area[0].set_value("Reset my password")
        with patch("main.create_model", return_value=fake_model()):
            self.submit(app)
        with patch("main.create_model") as factory:
            self.submit(app)
        factory.assert_not_called()
        app.checkbox[0].set_value(True)
        with patch("main.create_model", return_value=fake_model()) as factory:
            self.submit(app)
        factory.assert_called_once()
        self.assertFalse(app.exception)

    def test_ui_output_change_reuses_tests_for_only_the_added_report(self):
        app = self.app()
        app.text_area[0].set_value("Reset my password")
        with patch("main.create_model", return_value=fake_model()):
            self.submit(app)
        app.pills[0].set_value(["regression_impact"]).run()
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [sample_reports()["regression_impact"]]
        with patch("main.create_model", return_value=model):
            self.submit(app)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 1)
        self.assertFalse(app.exception)

    def test_provider_change_clears_session_generation_context(self):
        app = self.app()
        app.text_area[0].set_value("Reset my password")
        with patch("main.create_model", return_value=fake_model()):
            self.submit(app)
        app.radio[0].set_value("OpenAI (GPT-4o)").run()
        self.assertNotIn("generation_cache", app.session_state)

    def test_only_requested_report_is_displayed(self):
        app = self.app()
        app.pills[0].set_value(["regression_impact"]).run()
        app.text_area[0].set_value("Reset my password")
        report = RegressionImpact(summary="Login impact", areas=[{
            "area": "Login", "risk": "High", "reason": "Password changes affect login",
            "test_case_ids": ["TC-001"], "checks": ["Verify the new password works"], "assumptions": [],
        }])
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [sample_analysis(), sample_suite(), report]
        with patch("main.create_model", return_value=model):
            self.submit(app)
        self.assertFalse(app.exception)
        self.assertEqual([tab.label for tab in app.tabs], ["Regression Impact"])
        self.assertIn("Area", app.dataframe[0].value.columns)
        self.assertEqual(len(app.multiselect), 0)
        self.assertEqual(model.with_structured_output.call_count, 3)
        app.pills[0].set_value(["test_cases"]).run()
        self.assertEqual(len(app.tabs), 0)

    def test_selected_script_drafts_have_their_own_tabs(self):
        app = self.app()
        app.pills[0].set_value(["playwright", "rest_assured"]).run()
        app.text_area[0].set_value("Reset my password")
        reports = sample_reports()
        model = fake_model()
        model.with_structured_output.return_value.invoke.side_effect = [
            sample_analysis(), sample_suite(), reports["playwright"], reports["rest_assured"],
        ]
        with patch("main.create_model", return_value=model):
            self.submit(app)
        self.assertFalse(app.exception)
        self.assertEqual([tab.label for tab in app.tabs], ["Playwright Scripts", "REST Assured Tests"])
        self.assertEqual(app.code[0].value, reports["playwright"].code)
        self.assertEqual(app.code[1].value, reports["rest_assured"].code)
        self.assertEqual(len(app.warning), 2)
        self.assertEqual(model.with_structured_output.call_count, 4)

    def test_initial_page_and_blank_input(self):
        app = self.app()
        self.assertFalse(app.exception)
        self.submit(app)
        self.assertIn("non-empty", app.error[0].value)
        self.assertFalse(app.exception)

    def test_generate_and_filter(self):
        app = self.app()
        app.number_input[0].set_value(1)
        app.text_area[0].set_value("Reset my password")
        with patch("main.create_model", return_value=fake_model()):
            self.submit(app)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 5)
        app.multiselect[0].set_value(["API"]).run()
        self.assertEqual(len(app.dataframe[0].value), 1)
        app.multiselect[0].set_value([]).run()
        self.assertIn("No test cases", app.info[0].value)

    def test_sample_can_be_loaded(self):
        app = self.app()
        next(button for button in app.button if button.label == "Load sample").click().run()
        self.assertIn("registered customer", app.text_area[0].value)

    def test_failed_generation_clears_previous_result(self):
        app = self.app()
        app.number_input[0].set_value(1)
        app.text_area[0].set_value("Reset my password")
        with patch("main.create_model", return_value=fake_model()):
            self.submit(app)
        app.checkbox[0].set_value(True)
        with patch("main.create_model", side_effect=GenerationError("Set OPENAI_API_KEY")):
            self.submit(app)
        self.assertIn("OPENAI_API_KEY", app.error[0].value)
        self.assertEqual(len(app.dataframe), 0)

    def test_provider_selection_reaches_generation(self):
        app = self.app()
        self.assertEqual(app.radio[0].value, "Local AI (Ollama)")
        app.radio[0].set_value("OpenAI (GPT-4o)").run()
        self.assertEqual(app.text_input[0].label, "OpenAI API key")
        self.assertEqual(app.text_input[0].proto.type, TextInput.PASSWORD)
        app.text_input[0].set_value("session-test-key").run()
        app.text_area[0].set_value("Reset my password")
        with patch("main.create_model", return_value=fake_model()) as factory:
            self.submit(app)
        factory.assert_called_once_with("openai", None, api_key="session-test-key")
        self.assertFalse(app.exception)
        app.radio[0].set_value("Local AI (Ollama)").run()
        self.assertEqual(len(app.dataframe), 0)

    def test_clear_key_and_isolate_browser_sessions(self):
        first = self.app()
        first.radio[0].set_value("OpenAI (GPT-4o)").run()
        first.text_input[0].set_value("session-test-key").run()
        second = self.app()
        second.radio[0].set_value("OpenAI (GPT-4o)").run()
        self.assertEqual(second.text_input[0].value, "")
        next(button for button in first.button if button.label == "Clear API key").click().run()
        self.assertEqual(first.text_input[0].value, "")
        self.assertFalse(first.exception)

    def test_environment_key_is_not_sent_to_browser(self):
        with patch("main.load_dotenv"), patch.dict("os.environ", {"OPENAI_API_KEY": "configured-key"}):
            app = self.app()
            app.radio[0].set_value("OpenAI (GPT-4o)").run()
            self.assertEqual(app.text_input[0].value, "")

    def test_validate_key_button_and_status_reset(self):
        app = self.app()
        app.radio[0].set_value("OpenAI (GPT-4o)").run()
        app.text_input[0].set_value("session-test-key").run()
        with patch("main.validate_openai_key", return_value="gpt-4o") as validate:
            next(button for button in app.button if button.label == "Validate API key").click().run()
        validate.assert_called_once_with("session-test-key")
        self.assertIn("Key accepted", app.success[0].value)
        self.assertIn("not checked", app.success[0].value)
        app.text_input[0].set_value("replacement-test-key").run()
        self.assertEqual(len(app.success), 0)
        self.assertFalse(app.exception)

    def test_validation_failure_is_shown_in_sidebar(self):
        app = self.app()
        app.radio[0].set_value("OpenAI (GPT-4o)").run()
        app.text_input[0].set_value("session-test-key").run()
        with patch("main.validate_openai_key", side_effect=GenerationError("OpenAI rejected the API key.")):
            next(button for button in app.button if button.label == "Validate API key").click().run()
        self.assertIn("rejected the API key", app.error[0].value)
        self.assertFalse(app.exception)

    def test_ready_local_ai_needs_no_setup_buttons(self):
        app = self.app()
        self.assertFalse(app.exception)
        self.assertEqual(app.success[0].value, "Local AI is ready")
        labels = {button.label for button in app.button}
        self.assertNotIn("Check connection", labels)
        self.assertNotIn("Download AI model", labels)
        self.assertFalse(next(button for button in app.button if button.label == "Generate selected outputs").disabled)

    def test_unavailable_ollama_hides_model_download_and_blocks_generation(self):
        with patch("main.get_local_models", side_effect=GenerationError("Ollama unavailable")):
            app = self.app()
        self.assertIn("Step 1 of 2", app.warning[0].value)
        labels = {button.label for button in app.button}
        self.assertIn("Continue setup", labels)
        self.assertNotIn("Download AI model", labels)
        self.assertTrue(next(button for button in app.button if button.label == "Generate selected outputs").disabled)
        self.assertFalse(app.exception)

    def test_continue_setup_detects_ollama_without_model(self):
        with patch("main.get_local_models", side_effect=GenerationError("Ollama unavailable")):
            app = self.app()
        with patch("main.get_local_models", return_value=[]):
            next(button for button in app.button if button.label == "Continue setup").click().run()
        self.assertIn("Step 2 of 2", app.info[0].value)
        self.assertTrue(next(button for button in app.button if button.label == "Generate selected outputs").disabled)
        self.assertIn("Download AI model", {button.label for button in app.button})

    def test_download_model_button(self):
        with patch("main.get_local_models", return_value=[]):
            app = self.app()
        with patch("main.get_local_models", side_effect=[[], ["qwen2.5:7b"], ["qwen2.5:7b"]]):
            with patch("main.pull_local_model", return_value=iter([
                {"status": "downloading", "total": 100, "completed": 50}, {"status": "success"},
            ])) as download:
                next(button for button in app.button if button.label == "Download AI model").click().run()
        download.assert_called_once_with("qwen2.5:7b")
        self.assertEqual(app.success[0].value, "Local AI is ready")
        self.assertFalse(next(button for button in app.button if button.label == "Generate selected outputs").disabled)
        self.assertNotIn("Download AI model", {button.label for button in app.button})
        self.assertFalse(app.exception)


class DesktopBuildTests(unittest.TestCase):
    """Keep generated archives portable and exclude developer settings."""

    def test_qa_application_file_names_on_each_platform(self):
        root = Path("project")
        for system, bundle, executable in [
            ("Windows", "QAIntelligenceAgent", "QAIntelligenceAgent.exe"),
            ("Linux", "QAIntelligenceAgent", "QAIntelligenceAgent"),
            ("Darwin", "QA Intelligence Agent.app", "Contents/MacOS/QAIntelligenceAgent"),
        ]:
            with self.subTest(system=system), patch("build_desktop.platform.system", return_value=system):
                app, binary = bundle_paths(root)
                self.assertEqual(app, root / "dist" / bundle)
                self.assertEqual(binary, app / executable)

    def test_platform_labels(self):
        for system, machine, expected in [
            ("Windows", "AMD64", "windows-x64"), ("Linux", "x86_64", "linux-x64"),
            ("Darwin", "arm64", "macos-arm64"), ("Darwin", "x86_64", "macos-x64"),
        ]:
            with self.subTest(expected=expected), patch("build_desktop.platform.system", return_value=system):
                with patch("build_desktop.platform.machine", return_value=machine):
                    self.assertEqual(platform_label(), expected)

    def test_archive_contains_app_not_project_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "dist" / "QAIntelligenceAgent"
            bundle.mkdir(parents=True)
            (bundle / "QAIntelligenceAgent.exe").write_bytes(b"test executable")
            (root / ".env").write_text("OPENAI_API_KEY=private-key", encoding="utf-8")
            with patch("build_desktop.platform.system", return_value="Windows"):
                with patch("build_desktop.platform.machine", return_value="AMD64"):
                    archive = archive_bundle(root)
            with zipfile.ZipFile(archive) as content:
                self.assertEqual(content.read("QAIntelligenceAgent/QAIntelligenceAgent.exe"), b"test executable")
                self.assertFalse(any(name.endswith(".env") for name in content.namelist()))
            self.assertTrue(archive.with_name(archive.name + ".sha256").is_file())


class LauncherTests(unittest.TestCase):
    """Verify portable path selection, readiness, and child process cleanup."""

    def test_bundled_server_uses_executable_not_system_python(self):
        with patch("launch.sys.frozen", True, create=True):
            self.assertEqual(server_command(8503), [sys.executable, "--serve", "--port", "8503"])
            self.assertEqual(project_python(ROOT), Path(sys.executable))

    def test_internal_server_mode_calls_streamlit_bootstrap(self):
        with patch("streamlit.web.bootstrap.run") as bootstrap, patch("streamlit.web.bootstrap.load_config_options") as configuration:
            self.assertEqual(launch_app(["--serve", "--port", "8503"]), 0)
            flags = bootstrap.call_args.args[3]
            self.assertEqual(flags["server.address"], "127.0.0.1")
            self.assertEqual(flags["server.port"], 8503)
            self.assertEqual(flags["server.fileWatcherType"], "none")
            self.assertFalse(flags["global.developmentMode"])
            configuration.assert_called_once_with(flags)

    def test_project_venv_paths_for_windows_and_unix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("launch.sys.prefix", "base"), patch("launch.sys.base_prefix", "base"):
                for windows, relative in [(True, "Scripts/python.exe"), (False, "bin/python")]:
                    candidate = root / ".venv" / relative
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    candidate.touch()
                    self.assertEqual(project_python(root, windows=windows), candidate)

    def test_active_environment_is_respected(self):
        with patch("launch.sys.prefix", "active"), patch("launch.sys.base_prefix", "base"):
            self.assertEqual(project_python(ROOT), Path(sys.executable))

    def test_old_system_python_can_delegate_to_supported_venv(self):
        interpreter = Path("supported-environment") / "python"
        with patch("launch.sys.version_info", (3, 9)):
            with patch("launch.project_python", return_value=interpreter):
                with patch("launch.subprocess.call", return_value=0) as execute:
                    self.assertEqual(launch_app(["--no-browser"]), 0)
                    self.assertEqual(execute.call_args.args[0][0], str(interpreter))

    def test_project_venv_is_selected_even_if_python_is_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            candidate.parent.mkdir(parents=True)
            # Windows may require privileges to create symlinks; interpreter path
            # identity must be preserved regardless of how the file is installed.
            if sys.platform == "win32":
                candidate.touch()
            else:
                candidate.symlink_to(sys.executable)
            with patch("launch.PROJECT_ROOT", root), patch("launch.sys.prefix", "base"), patch("launch.sys.base_prefix", "base"):
                with patch("launch.subprocess.call", return_value=0) as execute:
                    self.assertEqual(launch_app(["--no-browser"]), 0)
                    self.assertEqual(execute.call_args.args[0][0], str(candidate))

    def test_occupied_port_is_skipped(self):
        with patch("launch.socket.socket") as constructor:
            constructor.return_value.__enter__.return_value.bind.side_effect = [OSError(), None]
            self.assertEqual(available_port(), 8502)

    def test_server_readiness_uses_health_endpoint(self):
        process = Mock()
        process.poll.return_value = None
        with patch("launch.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b"ok"
            self.assertTrue(wait_for_server(process, "http://127.0.0.1:8502"))
            opener.return_value.open.assert_called_once_with("http://127.0.0.1:8502/_stcore/health", timeout=1)

    def test_failed_server_is_not_opened(self):
        process = Mock()
        process.poll.return_value = 1
        self.assertFalse(wait_for_server(process, "http://127.0.0.1:8502"))

    def test_stubborn_server_is_killed(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("streamlit", 5), 0]
        stop_server(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_launcher_opens_browser_and_cleans_up(self):
        with patch("launch.project_python", return_value=Path(sys.executable)):
            with patch("launch.importlib.util.find_spec", return_value=Mock()):
                with patch("launch.subprocess.Popen") as constructor, patch("launch.wait_for_server", return_value=True):
                    with patch("launch.webbrowser.open", return_value=True) as browser:
                        process = constructor.return_value
                        process.wait.return_value = 0
                        process.poll.return_value = 0
                        self.assertEqual(launch_app(["--port", "8502"]), 0)
                        browser.assert_called_once_with("http://127.0.0.1:8502")
                        command = constructor.call_args.args[0]
                        self.assertIn("127.0.0.1", command)
                        self.assertIn(str(ROOT / "ui" / "streamlit_app.py"), command)


if __name__ == "__main__":
    unittest.main()