"""Output Formatter Agent: produce consistent tables without an LLM call."""

import csv
import io
from collections.abc import Mapping, Sequence
from typing import Any


COLUMNS = (
    "ID", "Category", "Title", "Priority", "Requirement", "Preconditions",
    "Steps", "Test Data", "Expected Result", "Assumptions",
)


def markdown_cell(value: str) -> str:
    """Keep generated content inside one Markdown table cell."""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def rows_to_csv(rows: Sequence[Mapping[str, str]]) -> str:
    """Export CSV with spreadsheet formula injection protection."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for row in rows:
        safe_row = {}
        for column in COLUMNS:
            value = row[column]
            # Spreadsheet software may interpret generated text as a formula.
            if value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe_row[column] = value
        writer.writerow(safe_row)
    return output.getvalue()


def format_output(state: Mapping[str, Any]) -> dict[str, Any]:
    """LangGraph node converting validated test cases to rows and Markdown."""
    rows = []
    for index, case in enumerate(state["test_cases"], start=1):
        rows.append({
            "ID": f"TC-{index:03d}",
            "Category": case.category,
            "Title": case.title,
            "Priority": case.priority,
            "Requirement": "\n".join(case.requirement_refs),
            "Preconditions": "\n".join(case.preconditions) or "None",
            "Steps": "\n".join(f"{number}. {step}" for number, step in enumerate(case.steps, 1)),
            "Test Data": case.test_data,
            "Expected Result": case.expected_result,
            "Assumptions": "\n".join(case.assumptions) or "None",
        })
    table = ["| " + " | ".join(COLUMNS) + " |", "| " + " | ".join(["---"] * len(COLUMNS)) + " |"]
    table.extend("| " + " | ".join(markdown_cell(row[column]) for column in COLUMNS) + " |" for row in rows)
    return {"rows": rows, "table": "\n".join(table)}