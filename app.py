"""Lesson 5: reusable configuration, real Agents API sessions, and a read-only tool."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import csv
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

BASE = Path(__file__).resolve().parent
SALES_TOOL = {
    "type": "function",
    "name": "get_sales_total",
    "description": "Read revenue, cost, and currency for one month from the application's sample sales data. Missing values are null. This tool cannot modify records.",
    "parameters": {
        "type": "object",
        "properties": {"period": {"type": "string", "description": "Calendar month in YYYY-MM format."}},
        "required": ["period"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class AgentConfig:
    model: str
    instructions: str
    reasoning_effort: str | None = None
    verbosity: str | None = None
    enable_sales_tool: bool = True

    @classmethod
    def load(cls, path: Path) -> AgentConfig:
        config = cls(**json.loads(path.read_text(encoding="utf-8-sig")))
        if not isinstance(config.model, str) or not config.model.strip():
            raise ValueError("model must be a nonempty string")
        if not isinstance(config.instructions, str) or not config.instructions.strip():
            raise ValueError("instructions must be a nonempty string")
        if config.reasoning_effort not in (None, "low", "medium", "high"):
            raise ValueError("reasoning_effort must be low, medium, high, or null")
        if config.verbosity not in (None, "low", "medium", "high"):
            raise ValueError("verbosity must be low, medium, high, or null")
        if type(config.enable_sales_tool) is not bool:
            raise ValueError("enable_sales_tool must be true or false")
        return config

    def api_agent(self) -> dict:
        """Only reusable behavior goes here; task and environment stay outside."""
        agent = {"model": self.model, "instructions": self.instructions}
        agent["tools"] = [SALES_TOOL] if self.enable_sales_tool else []
        if self.reasoning_effort is not None:
            agent["reasoning"] = {"effort": self.reasoning_effort}
        if self.verbosity is not None:
            agent["text"] = {"format": {"type": "text"}, "verbosity": self.verbosity}
        return agent


def validate_period(period: str) -> str:
    if not isinstance(period, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise ValueError("period must be a calendar month in YYYY-MM format")
    return period


def read_sales(period: str, data_file: Path) -> dict:
    """The application chooses the file; the model only chooses a period."""
    validate_period(period)
    with data_file.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    matches = [row for row in rows if row["period"] == period]
    if len(matches) > 1:
        raise ValueError("Conflicting data: more than one row for this period")
    if not matches:
        return {"found": False, "period": period, "missing": ["revenue", "cost", "currency"]}
    row = matches[0]
    result = {"found": True, "period": period, "currency": row.get("currency") or None}
    for name in ("revenue", "cost"):
        value = row.get(name)
        if not value:
            result[name] = None
            continue
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError(f"{name} must be finite")
        result[name] = str(number)
    result["missing"] = [name for name in ("revenue", "cost", "currency") if result[name] is None]
    return result


def handle_action(action: dict, config: AgentConfig, data_file: Path) -> dict:
    """Enforce capabilities in code, then construct an Agents API tool result."""
    result = {"type": "agent.session.input.tool_result",
              "turn_id": action["turn_id"], "call_id": action["call_id"]}
    try:
        if not config.enable_sales_tool or action["name"] != "get_sales_total":
            raise ValueError("This function is not enabled")
        arguments = action["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict) or set(arguments) != {"period"}:
            raise ValueError("Expected exactly one argument: period")
        output = read_sales(arguments["period"], data_file)
        result.update(success=True, output=json.dumps(output))
    except (ValueError, KeyError, OSError, InvalidOperation) as error:
        result.update(success=False, error=str(error))
    return result


def demo_report(data: dict) -> str:
    """Deterministic teaching simulation, not AI-generated text."""
    missing = data.get("missing", [])
    if not data.get("found") or missing:
        return (f"- Period: {data['period']}. Missing: {', '.join(missing)}.\n"
                "- Gross profit and margin cannot be calculated with the available data.\n"
                "- Supply the missing figures; no values have been invented.")
    revenue, cost = Decimal(data["revenue"]), Decimal(data["cost"])
    profit = revenue - cost
    margin = f"{profit / revenue * 100:.2f}%" if revenue else "undefined (zero revenue)"
    return (f"- {data['period']}: revenue {revenue:.2f} {data['currency']}; cost {cost:.2f} {data['currency']}.\n"
            f"- Gross profit = {revenue:.2f} - {cost:.2f} = {profit:.2f} {data['currency']}; gross margin = {margin}.\n"
            "- Based only on the supplied sample data; records were not changed.")


def run_demo(config: AgentConfig, period: str, data_file: Path, record: dict) -> None:
    record.update(session_id="demo_" + uuid4().hex, status="running")
    if config.enable_sales_tool:
        action = {"type": "function_call", "turn_id": "demo_turn", "call_id": "demo_call",
                  "name": "get_sales_total", "arguments": {"period": period}}
        result = handle_action(action, config, data_file)
        record["events"].extend([action, result])
        if not result["success"]:
            raise ValueError(result["error"])
        data = json.loads(result["output"])
    else:
        data = {"period": period, "found": False, "missing": ["revenue", "cost", "currency"]}
    record.update(status="completed", output_text=demo_report(data))


def run_live(client, config: AgentConfig, task: str, data_file: Path,
             record: dict, save, timeout_seconds: float = 180) -> None:
    """Consume actual session events and return pending function results."""
    started = time.monotonic()
    completed = False
    submitted = set()
    with client.beta.agents.sessions.create(
        agent=config.api_agent(), environment={"type": "none"}, input=task, stream=True,
    ).with_result_collection() as stream:
        for event in stream:
            item = event.to_dict()
            record["events"].append(item)
            session = item.get("session") or {}
            session_id = session.get("id") or item.get("session_id")
            if session_id:
                record["session_id"] = session_id
            save()
            print(f"Event: {item['type']}", flush=True)
            if time.monotonic() - started > timeout_seconds:
                raise TimeoutError("Local observation deadline exceeded; inspect the saved session ID. The remote turn may still be running.")
            if item["type"] == "agent.session.requires_action":
                for action in session.get("required_actions", []):
                    if action.get("type") != "function_call":
                        raise RuntimeError(f"Unsupported required action: {action.get('type')}")
                    key = (action["turn_id"], action["call_id"])
                    if key in submitted:
                        continue
                    result = handle_action(action, config, data_file)
                    record["events"].append({"local_tool_result": result})
                    save()
                    client.beta.agents.sessions.events.create(record["session_id"], events=[result])
                    submitted.add(key)
            if item["type"] in ("agent.session.turn.failed", "agent.session.turn.cancelled"):
                raise RuntimeError(f"Remote outcome: {item['type']}. Inspect the saved events.")
            if item["type"] == "agent.session.turn.completed":
                completed = True
        result = stream.get_final_result()
        record["session_id"] = result.session_id
        record["output_text"] = result.output_text
    if not completed or not record.get("output_text", "").strip():
        raise RuntimeError("No completed turn with text output was received; inspect the session events.")
    record["status"] = "completed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--period", default="2026-01")
    parser.add_argument("--config", type=Path, default=BASE / "config.json")
    parser.add_argument("--data", type=Path, default=BASE / "data" / "sales.csv")
    parser.add_argument("--model", help="Override the configured model for this run")
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--output", type=Path, default=BASE / "output")
    args = parser.parse_args()
    record = None
    try:
        validate_period(args.period)
        config = AgentConfig.load(args.config)
        if args.model:
            config = replace(config, model=args.model)
        if args.no_tools:
            config = replace(config, enable_sales_tool=False)
        task = f"Report revenue, cost, gross profit, and gross margin for {args.period}."
        args.output.mkdir(parents=True, exist_ok=True)
        destination = args.output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:8] + ".json")
        record = dict(mode=args.mode, status="starting", session_id=None,
                      configuration=asdict(config), environment={"type": "none"}, input=task,
                      data_file=str(args.data.resolve()), events=[])
        def save():
            destination.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        save()
        print(f"Mode: {args.mode}; model: {config.model}; sales tool: {config.enable_sales_tool}")
        print(f"Run log: {destination}")
        if args.mode == "demo":
            print("OFFLINE SIMULATION: no API requests; model and instruction changes do not affect demo text.")
            run_demo(config, args.period, args.data, record)
        else:
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("Set OPENAI_API_KEY in your shell before running live mode.")
            try:
                from openai import OpenAI
            except ImportError:
                raise ValueError("Install the SDK: python -m pip install --upgrade -r requirements.txt") from None
            with OpenAI(timeout=180.0, max_retries=0) as client:
                if not hasattr(client.beta, "agents"):
                    raise ValueError("This SDK lacks beta.agents. Upgrade openai to a version supporting the Agents API.")
                run_live(client, config, task, args.data, record, save)
        save()
        print(f"Session ID: {record['session_id']}\nStatus: {record['status']}\n\n{record['output_text']}")
        return 0
    except Exception as error:
        if record is not None:
            record.update(status="failed", error=str(error))
            save()
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
