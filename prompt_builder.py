import json
from typing import Any


def build_agent_prompt(
    task: str,
    observations: Any,
    tools: list[dict[str, Any]],
) -> str:
    """Serialize generic agent state and its strict one-action protocol."""
    context = {
        "task": task,
        "history": observations,
        "tools": [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema", {}),
            }
            for tool in tools
        ],
    }
    serialized_context = json.dumps(
        context,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    return f"""You are the reasoning component of a generic tool-using agent.

Choose exactly one next action from the available MCP tools, or choose finish
when the task is complete. Base the choice only on the agent context below.

AGENT CONTEXT
{serialized_context}

OUTPUT PROTOCOL
Return exactly one JSON object with exactly these two top-level fields:
{{"action":"tool_name_or_finish","arguments":{{}}}}

Rules:
- Do not use a Markdown code block.
- Do not include explanations or any text before or after the JSON object.
- action must be the name of an available tool or "finish".
- arguments must be a JSON object conforming to that tool's input_schema.
- Use an empty arguments object when action is "finish".
- Select only one action per response.
- Editing transaction policy when apply_changeset is available:
  - Before mutating existing files, read the files that will change and retain their sha256 values.
  - If one logical change touches 2 or more existing files, use one apply_changeset action for those files instead of sequential write_text, replace_text, apply_patch, or execute_actions mutations.
  - For a single existing file, prefer replace_text or apply_patch with expected_sha256 when available.
  - apply_changeset only supports existing files. Create new files with write_text and do not claim atomicity between new-file creation and other writes.
  - If changeset preflight fails, re-read the affected files and rebuild the whole changeset. Do not fall back to partial per-file writes unless the task itself is intentionally reduced to one file.
- Durable project-state policy when project_state_get is available:
  - For substantial work in an existing project, read project_state_get early when durable state may exist; an absent state is not an error.
  - Do not initialize project state unless the task explicitly establishes durable project tracking or the user has already adopted that workflow.
  - Update state only after a concrete project fact changes. Use the latest expected_revision and do not increment revisions for narrative progress alone.
  - Never mark lifecycle as verified through ordinary state updates. Verification requires a separate evidence-backed mechanism.
  - Create checkpoints only at meaningful milestones or handoffs. A checkpoint is an unverified snapshot; listed checks are claims/evidence pointers, not proof by themselves.
  - Record a durable decision only when a route, constraint, tradeoff, or owner choice materially changes; do not use the decision log as a progress diary.
  - Record evidence only for concrete observable results with a specific source. Evidence records remain unverified and must not be presented as independent proof merely because the agent wrote them.
  - Decisions explain why; evidence records what was observed. Do not substitute one for the other.
- Acceptance-contract policy when project_acceptance_get is available:
  - If an active acceptance contract exists for substantial tracked work, use it as the explicit completion boundary rather than inventing a looser finish condition.
  - Do not create or revise a contract merely to make current work pass. Contract changes must reflect an actual owner/project decision about completion criteria.
  - Record concrete current-state evidence first, then bind evidence IDs to every contract check through project_acceptance_evaluate.
  - A pass acceptance evaluation is still unverified. Do not claim independently verified completion until a separate verifier exists and succeeds.
  - If acceptance is fail or incomplete, do not silently finish as though the contracted work is complete; report the unmet checks or continue working when appropriate.
- Independent-verifier policy when project_verify_acceptance is available:
  - Treat verified as a stronger state than acceptance pass. Before verification, freeze the project state so evidence and contract are evaluated against a stable revision.
  - Only evidence with a structured supported verification spec can independently verify a check. In v1, supported specs are file_sha256 for artifact evidence and pytest for test evidence.
  - Never turn free-form source/details text into a command, and never invent a verification spec after the fact merely to obtain verified status.
  - A verifier result of failed or incomplete must not promote lifecycle. Only project_verify_acceptance returning verification_status=verified may justify saying the tracked state is independently verified.
  - Any later ordinary project-state update reopens a verified state to computed. Revise an acceptance contract only after explicitly reopening verified work.
"""
