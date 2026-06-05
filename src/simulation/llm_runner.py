"""LLM-agent simulation runner."""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any, Callable, Protocol

from contracts.study_spec import ArmSpec, StudySpec
from llm.client import LLMChatResult, LLMUsage
from simulation.agent_factory import SyntheticAgent
from simulation.runner import TrialResult


class ChatCompletionClient(Protocol):
    def chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMChatResult:
        ...


@dataclass(slots=True)
class LLMTrialRecord:
    study_id: str
    scenario: str
    agent_id: str
    arm: str
    model: str
    response_score: float
    response_label: str
    brief_reason: str
    prompt_messages: list[dict[str, str]]
    raw_response: str
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class LLMScenarioOutput:
    trials: list[TrialResult]
    records: list[LLMTrialRecord]
    usage: LLMUsage


def _find_arm(spec: StudySpec, arm_id: str) -> ArmSpec:
    for arm in spec.arms:
        if arm.arm_id == arm_id:
            return arm
    return spec.arms[0]


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM participant response must be a JSON object")
    return data


def _score_from_payload(payload: dict[str, Any]) -> float:
    raw_score = payload.get("response_score")
    if raw_score is None:
        raw_score = payload.get("score")
    score = float(raw_score)
    return min(100.0, max(0.0, score))


def _agent_profile_text(agent: SyntheticAgent) -> str:
    return "\n".join(
        [
            f"- Agent ID: {agent.agent_id}",
            f"- Age bucket: {agent.age_bucket}",
            f"- Gender: {agent.gender}",
            f"- Education: {agent.education}",
            f"- Income bucket: {agent.income_bucket}",
            f"- Region: {agent.region}",
            f"- Political orientation: {agent.political_orientation}",
            f"- Baseline compliance: {agent.baseline_compliance:.2f}",
            f"- Prosociality: {agent.prosociality:.2f}",
            f"- Risk preference: {agent.risk_preference:.2f}",
        ]
    )


def _messages_for_trial(spec: StudySpec, agent: SyntheticAgent, arm: ArmSpec, scenario: str) -> list[dict[str, str]]:
    system = (
        "You are simulating one human study participant for replication-triage research. "
        "Role-play only the participant described by the profile. Do not mention that you are an AI. "
        "Return JSON only."
    )
    user = f"""Study title:
{spec.title}

Extracted hypothesis:
{spec.hypothesis}

Participant population:
{spec.methodology.participant_population}

Recruitment/context:
{spec.methodology.recruitment_context}

Procedure summary:
{spec.methodology.procedure_summary}

Randomization/assignment:
{spec.methodology.assignment_procedure}

Scenario:
{scenario}

Participant profile:
{_agent_profile_text(agent)}

Condition shown to this participant:
{arm.prompt_template}

Outcome measure:
{spec.outcome_measure}

Respond as JSON only:
{{
  "response_score": number from 0 to 100,
  "response_label": "short natural-language answer",
  "brief_reason": "one short sentence explaining the participant-like response"
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class LLMParticipantRunner:
    def __init__(
        self,
        *,
        client: ChatCompletionClient,
        model: str,
        seed: int,
        scenario: str,
        temperature: float = 0.2,
        max_tokens: int = 180,
        max_retries: int = 2,
    ) -> None:
        self._client = client
        self._model = model
        self._rng = random.Random(seed)
        self._scenario = scenario
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_retries = max_retries

    def _run_trial(
        self,
        spec: StudySpec,
        agent: SyntheticAgent,
        arm_id: str,
    ) -> tuple[TrialResult, LLMTrialRecord, LLMUsage]:
        arm = _find_arm(spec, arm_id)
        messages = _messages_for_trial(spec, agent, arm, self._scenario)
        last_error: Exception | None = None
        for _ in range(self._max_retries + 1):
            try:
                response = self._client.chat_completion(
                    messages=messages,
                    model=self._model,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                parsed = _parse_json_response(response.content)
                score = _score_from_payload(parsed)
                trial = TrialResult(
                    study_id=spec.study_id,
                    arm=arm_id,
                    agent_id=agent.agent_id,
                    response_score=score,
                    scenario=self._scenario,
                )
                record = LLMTrialRecord(
                    study_id=spec.study_id,
                    scenario=self._scenario,
                    agent_id=agent.agent_id,
                    arm=arm_id,
                    model=response.model,
                    response_score=score,
                    response_label=str(parsed.get("response_label", ""))[:500],
                    brief_reason=str(parsed.get("brief_reason", ""))[:500],
                    prompt_messages=messages,
                    raw_response=response.content,
                    usage=response.usage.to_dict(),
                )
                return trial, record, response.usage
            except Exception as exc:
                last_error = exc
        raise ValueError(f"LLM participant response could not be parsed after retries: {last_error}") from last_error

    def run_sequential(
        self,
        spec: StudySpec,
        agents: list[SyntheticAgent],
        max_n: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> LLMScenarioOutput:
        picked = self._rng.sample(agents, k=min(max_n, len(agents)))
        trials: list[TrialResult] = []
        records: list[LLMTrialRecord] = []
        usage = LLMUsage()
        total = len(picked)
        arm_ids = [arm.arm_id for arm in spec.arms]
        assignments = [arm_ids[idx % len(arm_ids)] for idx in range(total)]
        self._rng.shuffle(assignments)
        if progress_callback is not None:
            progress_callback(0, total)
        for idx, (agent, arm_id) in enumerate(zip(picked, assignments), start=1):
            trial, record, trial_usage = self._run_trial(spec, agent, arm_id)
            trials.append(trial)
            records.append(record)
            usage.prompt_tokens += trial_usage.prompt_tokens
            usage.completion_tokens += trial_usage.completion_tokens
            usage.total_tokens += trial_usage.total_tokens
            if progress_callback is not None:
                progress_callback(idx, total)

        by_arm: dict[str, list[float]] = {"control": [], "treatment": []}
        for trial in trials:
            by_arm.setdefault(trial.arm, []).append(trial.response_score)
        if not by_arm.get("control") or not by_arm.get("treatment"):
            raise ValueError("LLM simulation did not produce both control and treatment samples")
        # Keep the effect estimate easy to inspect in debugger/logs through the records artifact.
        _ = mean(by_arm["treatment"]) - mean(by_arm["control"])
        return LLMScenarioOutput(trials=trials, records=records, usage=usage)


def records_to_dicts(records: list[LLMTrialRecord]) -> list[dict[str, Any]]:
    return [asdict(record) for record in records]
