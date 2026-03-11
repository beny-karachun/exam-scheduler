"""Gemini AI service wrappers for the Exam Scheduling System.

Three AI-powered functions that use the google-genai SDK with
Pydantic structured outputs:

1. parse_external_schedule      — Multimodal PDF/image → blackout windows
2. parse_natural_language_constraint — NLP → solver parameters
3. explain_infeasibility        — Math deadlock → plain-English explanation
"""

from __future__ import annotations

import base64
import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# ── Pydantic Schemas for Structured Outputs ───────────────────

class ExternalScheduleEntry(BaseModel):
    """A single extracted external exam time window."""
    course_code: str = Field(description="The course code exactly as provided in the input list.")
    start_time: str = Field(description="Exam start time in ISO 8601 format (e.g. 2026-03-16T09:00:00).")
    end_time: str = Field(description="Exam end time in ISO 8601 format (e.g. 2026-03-16T12:00:00).")


class ExternalScheduleResponse(BaseModel):
    """List of extracted exam entries."""
    entries: list[ExternalScheduleEntry] = Field(
        description="List of extracted exam schedule entries for the requested course codes only."
    )


class TimeOfDay(str, Enum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"


class NLPConstraintResult(BaseModel):
    """Structured constraint parsed from natural language."""
    course_code: str | None = Field(
        None,
        description="The course code or name mentioned by the user.",
    )
    preferred_day: str | None = Field(
        None,
        description="Preferred day of the week (e.g. 'Monday', 'Tuesday'). Null if not specified.",
    )
    time_of_day: TimeOfDay | None = Field(
        None,
        description="Preferred time of day. Null if not specified.",
    )
    requires_computers: bool = Field(
        False,
        description="True if the request mentions needing a computer lab or special equipment.",
    )


class InfeasibilityExplanation(BaseModel):
    """Plain-English explanation of a solver deadlock."""
    explanation_text: str = Field(
        description=(
            "A clear, non-technical explanation of why the schedule is impossible, "
            "written for a university Dean who is not a mathematician."
        ),
    )
    actionable_options: list[str] = Field(
        description=(
            "Exactly 3 concrete, actionable options the Dean can take to resolve "
            "the scheduling deadlock (e.g., split cohort, override transit buffer, "
            "move an exam to a different week)."
        ),
    )


# ── Gemini Client Factory ────────────────────────────────────

def _get_client():
    """Lazily import and create the google-genai client."""
    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. Set it in .env or environment variables."
        )
    from google import genai
    return genai.Client(api_key=settings.GEMINI_API_KEY)


# ── 1. Multimodal PDF/Image Parser ───────────────────────────

async def parse_external_schedule(
    file_bytes: bytes,
    mime_type: str,
    external_course_codes: list[str],
) -> list[dict[str, Any]]:
    """
    Extract exam dates/times for specific external course codes from
    a messy university schedule document (PDF or image).

    Uses Gemini multimodal input with structured JSON output.
    """
    client = _get_client()

    codes_str = ", ".join(external_course_codes)
    prompt = (
        "You are an expert at reading university exam schedules.\n\n"
        f"I am uploading a document (PDF or image) that contains the central "
        f"university exam schedule. Extract the exact exam dates, start times, "
        f"and end times ONLY for these specific course codes: {codes_str}.\n\n"
        f"Ignore all other courses in the document. If a course code from the "
        f"list is not found in the document, do NOT include it in the output.\n\n"
        f"Return the results as a JSON object with an 'entries' array."
    )

    # Build multimodal content: file + text prompt
    file_part = {
        "inline_data": {
            "mime_type": mime_type,
            "data": base64.standard_b64encode(file_bytes).decode("ascii"),
        }
    }

    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[file_part, prompt],
        config={
            "response_mime_type": "application/json",
            "response_json_schema": ExternalScheduleResponse.model_json_schema(),
        },
    )

    parsed = ExternalScheduleResponse.model_validate_json(response.text)
    logger.info("Extracted %d external schedule entries via Gemini", len(parsed.entries))
    return [entry.model_dump() for entry in parsed.entries]


# ── 2. Natural Language → Constraint Parser ───────────────────

async def parse_natural_language_constraint(text: str) -> dict[str, Any]:
    """
    Translate a Dean's natural language scheduling request into
    structured solver constraint parameters.

    Example: "Schedule Genetics 401 on Tuesday morning in a computer lab"
    → {course_code: "Genetics 401", preferred_day: "Tuesday",
       time_of_day: "MORNING", requires_computers: true}
    """
    client = _get_client()

    prompt = (
        "You are a scheduling assistant for a university faculty.\n\n"
        "Translate the following natural language scheduling request into "
        "strict, structured scheduling parameters. Extract the course name "
        "or code, any preferred day of the week, time of day preference "
        "(MORNING = before 12:00, AFTERNOON = 12:00-17:00, EVENING = after 17:00), "
        "and whether the request mentions needing computers, a computer lab, "
        "or special equipment.\n\n"
        "If something is not mentioned, leave it as null.\n\n"
        f'Request: "{text}"'
    )

    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": NLPConstraintResult.model_json_schema(),
        },
    )

    parsed = NLPConstraintResult.model_validate_json(response.text)
    logger.info("Parsed NLP constraint: %s", parsed.model_dump())
    return parsed.model_dump()


# ── 3. Infeasibility Explainer ("The Hostage Negotiator") ─────

async def explain_infeasibility(deadlock_context: dict[str, Any]) -> dict[str, Any]:
    """
    When OR-Tools returns INFEASIBLE, translate the mathematical
    deadlock into plain English with 3 actionable resolution options.

    `deadlock_context` should contain the solver's infeasibility_details
    dict (courses without rooms, heavily constrained students, etc.).
    """
    client = _get_client()

    prompt = (
        "You are a diplomatic university scheduling advisor.\n\n"
        "The automated exam scheduling system has determined that it is "
        "MATHEMATICALLY IMPOSSIBLE to schedule all exams under the current "
        "constraints. Below is the technical deadlock report from the "
        "constraint solver.\n\n"
        "Your task:\n"
        "1. Translate this deadlock into a clear, non-technical explanation "
        "   that a university Dean (who is not a mathematician) can understand.\n"
        "2. Provide exactly 3 concrete, actionable options the Dean can take "
        "   to resolve the deadlock. Options should be practical (e.g., split "
        "   an exam cohort into two rooms, override the 45-minute transit "
        "   buffer for specific students, extend the exam period by one day, "
        "   move a course to a different time slot).\n\n"
        f"Technical deadlock report:\n```json\n{deadlock_context}\n```"
    )

    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": InfeasibilityExplanation.model_json_schema(),
        },
    )

    parsed = InfeasibilityExplanation.model_validate_json(response.text)
    logger.info("Generated infeasibility explanation with %d options", len(parsed.actionable_options))
    return parsed.model_dump()
