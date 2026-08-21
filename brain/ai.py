#!/usr/bin/env python3
"""OpenAI integration for Brain structured analysis and Ask Brain."""

from __future__ import annotations

import json
import logging
from typing import Any

import dashboard.config as config

logger = logging.getLogger(__name__)

SIGNAL_PROMPT_VERSION = "brain-signals-v1"
ASK_PROMPT_VERSION = "brain-ask-v1"
BOOKING_HEALTH_PROMPT_VERSION = "brain-booking-health-v1"
STAY_OUTCOME_PROMPT_VERSION = "kpi-stay-outcome-v1"


class BrainAIClient:
    """Wrapper around OpenAI with JSON-schema outputs and a safe fallback."""

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        self.model = model or config.OPENAI_MODEL
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)

    def generate_signals(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate structured signal candidates from curated context."""
        schema = {
            "name": "brain_signal_response",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "signals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "title": {"type": "string"},
                                "category": {"type": "string"},
                                "severity": {"type": "string"},
                                "confidence": {"type": "number"},
                                "summary": {"type": "string"},
                                "why_it_matters": {"type": "string"},
                                "suggested_action": {"type": "string"},
                                "audience": {"type": "string", "enum": ["operator", "revenue"]},
                                "listing_id": {"type": ["integer", "null"]},
                                "reservation_id": {"type": ["integer", "null"]},
                                "evidence_ids": {"type": "array", "items": {"type": "integer"}},
                            },
                            "required": [
                                "title",
                                "category",
                                "severity",
                                "confidence",
                                "summary",
                                "why_it_matters",
                                "suggested_action",
                                "audience",
                                "listing_id",
                                "reservation_id",
                                "evidence_ids",
                            ],
                        },
                    }
                },
                "required": ["signals"],
            },
            "strict": True,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are STR Signal Brain. Surface only high-value, evidence-backed "
                    "signals for short-term rental operations and revenue management. "
                    "Use audience='operator' for all non-revenue team operations "
                    "signals and audience='revenue' for booking/pricing signals."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, default=str),
            },
        ]
        return self._chat_json(messages, schema)

    def generate_booking_health_analyses(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate one concise revenue-management opinion per listing."""
        schema = {
            "name": "brain_booking_health_response",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "analyses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "listing_id": {"type": "integer"},
                                "severity": {"type": "string"},
                                "confidence": {"type": "number"},
                                "booking_pattern": {"type": "string"},
                                "pricelabs_opinion": {"type": "string"},
                                "airbnb_page_opinion": {"type": "string"},
                                "opinion": {"type": "string"},
                                "action_items": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": [
                                "listing_id",
                                "severity",
                                "confidence",
                                "booking_pattern",
                                "pricelabs_opinion",
                                "airbnb_page_opinion",
                                "opinion",
                                "action_items",
                            ],
                        },
                    }
                },
                "required": ["analyses"],
            },
            "strict": True,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are STR Signal Brain's revenue operator. Produce concise, evidence-grounded "
                    "booking-health opinions for short-term rentals. Use internal listing_name, never "
                    "write 'Listing <id>'. If PriceLabs or Airbnb data is unavailable, say so and base "
                    "the recommendation on Hostaway/calendar evidence."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, default=str),
            },
        ]
        return self._chat_json(messages, schema)

    def classify_stay_outcomes(self, context: dict[str, Any]) -> dict[str, Any]:
        """Classify completed stays from captured Hostaway conversation evidence."""
        issue_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "issue_type": {
                    "type": "string",
                    "enum": [
                        "access_checkin",
                        "cleanliness_readiness",
                        "essential_amenity",
                        "maintenance",
                        "noise_neighbour",
                        "safety_security",
                        "listing_accuracy",
                        "missing_service_item",
                        "relocation_occupancy",
                        "communication_failure",
                        "other",
                    ],
                },
                "severity": {"type": "string", "enum": ["minor", "material", "critical"]},
                "description": {"type": "string"},
                "resolution_state": {"type": "string", "enum": ["resolved", "unresolved", "unclear"]},
                "resolution_evidence": {"type": "string"},
                "evidence_message_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": [
                "issue_type",
                "severity",
                "description",
                "resolution_state",
                "resolution_evidence",
                "evidence_message_ids",
            ],
        }
        schema = {
            "name": "stay_outcome_response",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stays": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "reservation_id": {"type": "integer"},
                                "outcome": {
                                    "type": "string",
                                    "enum": ["smooth", "recovered", "unresolved", "needs_review"],
                                },
                                "confidence": {"type": "number"},
                                "summary": {"type": "string"},
                                "issues": {"type": "array", "items": issue_schema},
                                "evidence_message_ids": {"type": "array", "items": {"type": "integer"}},
                            },
                            "required": [
                                "reservation_id",
                                "outcome",
                                "confidence",
                                "summary",
                                "issues",
                                "evidence_message_ids",
                            ],
                        },
                    }
                },
                "required": ["stays"],
            },
            "strict": True,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You classify completed short-term-rental stays only from the supplied Hostaway messages. "
                    "A routine question, request, thanks, preference, or early-check-in request is not a problem. "
                    "A material issue meaningfully affects access, safety, cleanliness, essential amenities, "
                    "property usability, or the accuracy of the booked stay. Minor inconveniences may be listed "
                    "but do not change a smooth outcome. Use recovered only when every material/critical issue has "
                    "credible resolution evidence in the conversation, preferably guest confirmation or a message "
                    "showing the remedy worked. A host saying something should be fixed is not sufficient by itself. "
                    "Use unresolved when any material/critical issue clearly remained open, repeated, or affected the "
                    "guest through departure. Use needs_review when evidence is missing, contradictory, or insufficient. "
                    "Do not infer sentiment, issue, or resolution beyond the supplied text. Cite only supplied message IDs."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, default=str),
            },
        ]
        return self._chat_json(messages, schema)

    def answer_question(self, question: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        """Answer Ask Brain with evidence citations."""
        schema = {
            "name": "brain_ask_response",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "evidence_id": {"type": "integer"},
                                "reason": {"type": "string"},
                            },
                            "required": ["evidence_id", "reason"],
                        },
                    },
                },
                "required": ["answer", "confidence", "citations"],
            },
            "strict": True,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Answer only from the provided evidence. If the evidence is insufficient, "
                    "say so directly and suggest what data is missing."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"question": question, "evidence": evidence}, default=str),
            },
        ]
        return self._chat_json(messages, schema)

    def _chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        request = {
            "model": self.model,
            "messages": messages,
        }
        if not self.model.startswith("gpt-5.6"):
            request["temperature"] = 0.2
        try:
            response = self.client.chat.completions.create(
                **request,
                response_format={"type": "json_schema", "json_schema": schema},
            )
        except Exception as exc:
            logger.warning("JSON schema response failed, retrying with json_object: %s", exc)
            response = self.client.chat.completions.create(
                **request,
                response_format={"type": "json_object"},
            )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        result["_usage"] = getattr(response, "usage", None).model_dump() if getattr(response, "usage", None) else None
        return result
