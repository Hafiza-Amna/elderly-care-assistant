# ruff: noqa
# Elderly Care Assistant — Multi-Agent Workflow (ADK 2.0 graph API)

import json
import re
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.apps import App
from google.adk.events import Event, EventActions, RequestInput
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioConnectionParams
from mcp.client.stdio import StdioServerParameters as McpStdioParams
from google.adk.workflow import Workflow
from google.genai.types import Content, Part

from app.config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Groq model with parallel_tool_calls=False ─────────────────────────────────
# Groq's Llama 3.3 70B can generate malformed XML-style tool calls when
# parallel_tool_calls is enabled. Disabling it forces sequential JSON tool use.
groq_model = LiteLlm(
    model=config.model,
    parallel_tool_calls=False,
)

# ── MCP Toolset (wired into medication_advisor and wellness_monitor) ──────────
_mcp_server_path = str(Path(__file__).parent / "mcp_server.py")
_python_exe = sys.executable

mcp_toolset = MCPToolset(
    connection_params=StdioConnectionParams(
        server_params=McpStdioParams(
            command=_python_exe,
            args=[_mcp_server_path],
        ),
    ),
)

# ──────────────────────────────────────────────────────────────────────────────
# Specialized LlmAgent Sub-Agents
# ──────────────────────────────────────────────────────────────────────────────

medication_advisor = LlmAgent(
    name="medication_advisor",
    model=groq_model,
    description="Handles medication schedules, reminders, and drug interaction queries for elderly patients.",
    instruction="""You are a compassionate medication management specialist for elderly care.
Your responsibilities:
- Retrieve and present medication schedules using the get_medication_schedule tool
- Add new medication reminders using the add_medication_reminder tool
- Check for drug interactions using the check_drug_interaction tool
- Flag potential interactions using simple, non-alarming language
- Remind patients about upcoming doses based on their schedule
- Answer questions about side effects in plain, easy-to-understand English
- Always recommend consulting a doctor for any changes

Format responses in a warm, clear style suitable for elderly users.
Keep explanations short and use bullet points when listing medications.
Always use the patient's name from the conversation context.""",
    tools=[mcp_toolset],
    output_key="medication_response",
)

wellness_monitor = LlmAgent(
    name="wellness_monitor",
    model=groq_model,
    description="Tracks wellness metrics, daily activity, and health observations for elderly patients.",
    instruction="""You are a caring wellness and health monitoring specialist for elderly care.
Your responsibilities:
- Log daily wellness metrics using the log_health_metric tool (blood pressure, glucose, weight, mood, pain)
- Retrieve and present wellness summaries using the get_wellness_summary tool
- Provide gentle health tips and lifestyle recommendations based on logged data
- Identify concerning trends and recommend professional consultation when needed
- Suggest daily activities and exercises appropriate for the patient's condition
- Track fall risks, mobility, and daily living activities

Always use the log_health_metric tool when the patient reports a measurement.
Always use the get_wellness_summary tool when asked for a summary or trend report.
Format responses warmly and positively. Celebrate progress. Flag serious concerns clearly.
Use simple language without medical jargon.""",
    tools=[mcp_toolset],
    output_key="wellness_response",
)

caregiver_updater = LlmAgent(
    name="caregiver_updater",
    model=groq_model,
    description="Composes professional, concise updates and alerts for caregivers and family members.",
    instruction="""You are a professional caregiver communication specialist.
Your responsibilities:
- Compose clear, professional status updates for caregivers and family members
- Write urgent alerts when patient health metrics are concerning
- Summarize daily wellness reports for caregiver review
- Draft appointment reminders and follow-up action items
- Maintain a caring yet professional tone in all communications

Format the update as:
- Patient Status: [brief status]
- Key Updates: [bullet points]
- Action Required: [what caregiver needs to do, if anything]
- Next Check-in: [suggested time]""",
    output_key="caregiver_update",
)

# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator LlmAgent (uses AgentTool to delegate to sub-agents)
# ──────────────────────────────────────────────────────────────────────────────

orchestrator = LlmAgent(
    name="orchestrator",
    model=groq_model,
    description="Main orchestrator that understands patient requests and delegates to specialized agents.",
    instruction="""You are the central orchestrator for an elderly care assistant system.
Analyze the patient's request and delegate to the appropriate specialist:

- Use `medication_advisor` for: medication questions, dose reminders, drug interactions, prescription info
- Use `wellness_monitor` for: logging health metrics, wellness tips, activity tracking, mood/pain check-ins
- Use `caregiver_updater` for: sending updates to family, caregiver alerts, status reports

Always:
1. Greet the user warmly by name if known
2. Confirm what action you are taking
3. Provide the specialist's response in a clear, friendly manner
4. Ask if there's anything else you can help with

Patient requests should be handled with patience, empathy, and clarity.""",
    tools=[
        AgentTool(agent=medication_advisor),
        AgentTool(agent=wellness_monitor),
        AgentTool(agent=caregiver_updater),
    ],
    output_key="orchestrator_response",
)

# ──────────────────────────────────────────────────────────────────────────────
# Workflow Function Nodes
# ──────────────────────────────────────────────────────────────────────────────

def security_checkpoint(node_input: str | None = None) -> Event:
    """Security node: PII scrubbing, prompt injection detection, audit logging."""
    cleaned = str(node_input) if node_input is not None else ""

    # ── PII Scrubbing ──────────────────────────────────────────────────────
    # Phone numbers
    cleaned = re.sub(r"\b(\+?\d[\d\s\-().]{7,}\d)\b", "[PHONE_REDACTED]", cleaned)
    # Email addresses
    cleaned = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL_REDACTED]", cleaned)
    # Social Security / ID numbers (e.g. 123-45-6789)
    cleaned = re.sub(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b", "[ID_REDACTED]", cleaned)
    # Credit / debit card numbers
    cleaned = re.sub(r"\b(?:\d[ -]?){13,16}\b", "[CARD_REDACTED]", cleaned)
    # Date of birth patterns
    cleaned = re.sub(r"\b(dob|date of birth)[:\s]+[\d/\-]+", "[DOB_REDACTED]", cleaned, flags=re.IGNORECASE)

    pii_detected = cleaned != str(node_input)

    # ── Prompt Injection Detection ─────────────────────────────────────────
    injection_keywords = [
        "ignore previous instructions",
        "disregard your instructions",
        "forget your prompt",
        "system prompt",
        "jailbreak",
        "act as",
        "pretend you are",
        "you are now",
        "override",
        "bypass",
    ]
    lower_input = cleaned.lower()
    injection_found = any(kw in lower_input for kw in injection_keywords)

    # ── Audit Log ──────────────────────────────────────────────────────────
    severity = "INFO"
    if pii_detected:
        severity = "WARNING"
    if injection_found:
        severity = "CRITICAL"

    audit_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "severity": severity,
        "pii_detected": pii_detected,
        "injection_detected": injection_found,
        "input_length": len(cleaned),
        "node": "security_checkpoint",
    }
    logger.info(json.dumps(audit_entry))

    # ── Domain Rule: block medical advice override attempts ────────────────
    medical_override_patterns = ["override medication", "change my prescription", "stop all medication"]
    medical_override = any(p in lower_input for p in medical_override_patterns)
    if medical_override:
        audit_entry["severity"] = "CRITICAL"
        audit_entry["reason"] = "medical_override_attempt"
        logger.warning(json.dumps(audit_entry))
        return Event(
            actions=EventActions(route="SECURITY_EVENT"),
            output="⚠️ Your request contains unsafe instructions. Please speak to your doctor directly for changes to your medication or treatment plan.",
        )

    if injection_found:
        return Event(
            actions=EventActions(route="SECURITY_EVENT"),
            output="⚠️ Your request could not be processed for security reasons. Please rephrase your question.",
        )

    # Safe — pass cleaned input to next node
    return Event(
        actions=EventActions(route="SAFE"),
        output=cleaned,
    )


def classify_request(node_input: Any = None) -> Event:
    """Classify patient intent to route to the right sub-workflow."""
    logger.info(f"DEBUG: classify_request received node_input type={type(node_input)} content={node_input}")
    
    text = ""
    if isinstance(node_input, Event):
        text = node_input.output or ""
    elif isinstance(node_input, dict):
        text = node_input.get("output") or ""
    elif isinstance(node_input, str):
        text = node_input
    
    # Fallback to checking attributes if it's some other object
    if not text and hasattr(node_input, "output") and node_input.output is not None:
        text = str(node_input.output)

    lower = text.lower()
    caregiver_keywords = [
        "notify caregiver", "send update", "tell my family", "contact nurse",
        "caregiver alert", "family update", "emergency contact", "send report",
    ]
    
    if any(kw in lower for kw in caregiver_keywords):
        return Event(
            actions=EventActions(route="CAREGIVER"),
            output=text,
        )

    return Event(
        actions=EventActions(route="ORCHESTRATE"),
        output=text,
    )




def caregiver_approval(node_input: str):
    """HITL node: pause and ask for human confirmation before contacting caregiver."""
    yield RequestInput(
        message=f"📋 You want to send an update to your caregiver.\n\nMessage preview:\n\"{node_input}\"\n\nShall I send this update? Reply YES to confirm or NO to cancel.",
    )


def build_caregiver_message(node_input: str) -> str:
    """Prepare the caregiver message text before delegating to caregiver_updater."""
    lower = node_input.strip().lower()
    if lower in ("no", "cancel", "n"):
        return "❌ Caregiver update cancelled. No message was sent."
    # Pass confirmation through to caregiver_updater agent node
    return node_input


def format_security_response(node_input) -> str:
    """Return the security block message."""
    if hasattr(node_input, "content") and node_input.content:
        parts = node_input.content.parts
        if parts:
            return parts[0].text or str(node_input)
    return str(node_input)


def start_node(node_input: Any = None) -> str:
    """Initial start node that passes the user input down to security_checkpoint.
    Handles cases where the input is a Content object, a string, or None.
    """
    if node_input is None:
        return ""
    if hasattr(node_input, "parts") and node_input.parts:
        try:
            return node_input.parts[0].text or ""
        except Exception:
            pass
    return str(node_input)




def final_output(node_input) -> str:
    """Collect and format the final response."""
    if isinstance(node_input, dict):
        return (
            node_input.get("orchestrator_response")
            or node_input.get("caregiver_update")
            or str(node_input)
        )
    return str(node_input)


# ──────────────────────────────────────────────────────────────────────────────
# Workflow Graph (ADK 2.0 Workflow API)
# EDGE RULE: never >1 edge between the same (source, target) pair.
# Converging routes share ONE unconditional terminal edge.
# ──────────────────────────────────────────────────────────────────────────────

root_agent = Workflow(
    name="elderly_care_workflow",
    description="Elderly care concierge that tracks medications, monitors wellness, and coordinates caregiver updates securely.",
    edges=[
        # Phase 1: Start node provides empty input
        ("START", start_node),
        (start_node, security_checkpoint),
        # Phase 2a: Security gate routes
        (security_checkpoint, {
            "SAFE": classify_request,
            "SECURITY_EVENT": format_security_response,
        }),
        # Phase 2b: Route safe requests
        (classify_request, {
            "ORCHESTRATE": orchestrator,
            "CAREGIVER": caregiver_approval,
        }),
        # Phase 3a: Orchestrator output → final
        (orchestrator, final_output),
        # Phase 3b: HITL → build message → caregiver_updater agent → final
        (caregiver_approval, build_caregiver_message),
        (build_caregiver_message, caregiver_updater),
        (caregiver_updater, final_output),
        # Phase 3c: Security block → final (single unconditional edge)
        (format_security_response, final_output),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
