"""Offline checks for calculations, tool boundaries, and the session event flow."""
from dataclasses import replace
import json
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import AgentConfig, BASE, demo_report, handle_action, read_sales, run_demo, run_live


class FakeEvent:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return self.value


class FakeStream:
    def __init__(self, events, text="A completed report"):
        self.events, self.text = events, text

    def with_result_collection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(FakeEvent(event) for event in self.events)

    def get_final_result(self):
        return SimpleNamespace(session_id="sess_test", output_text=self.text)


class FakeSessions:
    def __init__(self, stream):
        self.stream, self.results = stream, []
        self.events = SimpleNamespace(create=self.submit)

    def create(self, **kwargs):
        self.request = kwargs
        return self.stream

    def submit(self, session_id, *, events):
        self.results.append((session_id, events))


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.config = AgentConfig.load(BASE / "config.json")
        self.data = BASE / "data" / "sales.csv"
        self.action = dict(type="function_call", turn_id="turn_test", call_id="call_test",
                           name="get_sales_total", arguments={"period": "2026-01"})

    def test_expected_profit_and_margin(self):
        report = demo_report(read_sales("2026-01", self.data))
        self.assertIn("40.00 EUR", report)
        self.assertIn("40.00%", report)
        self.assertEqual(len(report.splitlines()), 3)

    def test_missing_data_and_unknown_period(self):
        for period in ("2026-03", "2026-12"):
            with self.subTest(period=period):
                self.assertIn("cannot be calculated", demo_report(read_sales(period, self.data)))

    def test_zero_revenue(self):
        report = demo_report(read_sales("2026-04", self.data))
        self.assertIn("-20.00 EUR", report)
        self.assertIn("undefined (zero revenue)", report)

    def test_tool_rejects_disabled_unknown_and_extra_arguments(self):
        self.assertFalse(handle_action(self.action, replace(self.config, enable_sales_tool=False), self.data)["success"])
        self.assertFalse(handle_action({**self.action, "name": "delete_sales"}, self.config, self.data)["success"])
        self.assertFalse(handle_action({**self.action, "arguments": {"period": "2026-01", "file": "secret"}}, self.config, self.data)["success"])
        self.assertFalse(handle_action({**self.action, "arguments": {"period": "2026-13"}}, self.config, self.data)["success"])

    def test_conflicting_rows_and_nonfinite_amounts(self):
        for rows in ("2026-01,100,60,EUR\n2026-01,200,80,EUR\n", "2026-01,NaN,60,EUR\n"):
            with patch("pathlib.Path.open", return_value=StringIO("period,revenue,cost,currency\n" + rows)):
                self.assertFalse(handle_action(self.action, self.config, self.data)["success"])

    def test_configuration_reused_in_independent_demo_sessions(self):
        first, second = {"events": []}, {"events": []}
        before = self.config.api_agent()
        run_demo(self.config, "2026-01", self.data, first)
        run_demo(self.config, "2026-02", self.data, second)
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertIn("60.00 EUR", second["output_text"])
        self.assertEqual(before, self.config.api_agent())

    def test_optional_settings_can_be_omitted(self):
        agent = replace(self.config, reasoning_effort=None, verbosity=None).api_agent()
        self.assertNotIn("reasoning", agent)
        self.assertNotIn("text", agent)
        self.assertNotIn("environment", agent)
        self.assertNotIn("input", agent)

    def fake_client(self, events, text="A completed report"):
        sessions = FakeSessions(FakeStream(events, text))
        return SimpleNamespace(beta=SimpleNamespace(agents=SimpleNamespace(sessions=sessions))), sessions

    def test_live_event_loop_returns_tool_result_once(self):
        pending = {"type": "agent.session.requires_action", "session": {"id": "sess_test", "required_actions": [self.action]}}
        client, sessions = self.fake_client([pending, pending, {"type": "agent.session.turn.completed"}])
        record = {"events": []}
        snapshots = []
        run_live(client, self.config, "Report January", self.data, record,
                 lambda: snapshots.append(json.dumps(record)))
        self.assertEqual(record["status"], "completed")
        self.assertEqual(len(sessions.results), 1)
        result = sessions.results[0][1][0]
        self.assertTrue(result["success"])
        self.assertEqual(result["call_id"], "call_test")
        self.assertEqual(sessions.request["environment"], {"type": "none"})
        self.assertEqual(sessions.request["input"], "Report January")
        self.assertTrue(snapshots)

    def test_failure_cancellation_and_incomplete_stream_are_not_success(self):
        for event_type in ("agent.session.turn.failed", "agent.session.turn.cancelled", "agent.session.created"):
            with self.subTest(event_type=event_type):
                client, _ = self.fake_client([{"type": event_type}])
                with self.assertRaises(RuntimeError):
                    run_live(client, self.config, "Report January", self.data, {"events": []}, lambda: None)


if __name__ == "__main__":
    unittest.main()
