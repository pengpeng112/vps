"""No-network tests: a scarce reset must never be consumed by a preflight."""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("reset_once", pathlib.Path(__file__).with_name("codex-reset-once.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.config = root / "config.json"
        self.log = root / "events.jsonl"
        self.config.write_text(json.dumps({"runAt": 1000, "stopAt": 1100, "expiresAt": 1200,
                                          "creditId": "selected", "idempotencyKey": "stable-logical-attempt"}))
        self.calls = []
        outer = self

        class FakeRPC:
            def __init__(self, config): pass
            def close(self): pass
            def call(self, method, params=None):
                outer.calls.append((method, params))
                if method.endswith("/consume"):
                    count = sum(m.endswith("/consume") for m, _ in outer.calls)
                    if getattr(outer, "lost_response", False) and count == 1:
                        raise TimeoutError()
                    return {"outcome": "alreadyRedeemed" if getattr(outer, "lost_response", False) else "reset"}
                consumed = any(m.endswith("/consume") for m, _ in outer.calls)
                rows = [{"id": "other", "status": "available", "expiresAt": 9000}]
                if not consumed:
                    rows.append({"id": "selected", "status": "available", "expiresAt": 1200})
                return {"rateLimitResetCredits": {"availableCount": len(rows), "credits": rows}}

        for obj, name, value in [(module, "CONFIG", self.config), (module, "LOG", self.log),
                                 (module, "RPC", FakeRPC)]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)

    def invoke(self, mode, now=1000):
        with patch("sys.argv", ["test", mode]), patch.object(module.time, "time", return_value=now), \
             patch.object(module.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            return module.main()

    def test_check_never_consumes(self):
        self.assertEqual(self.invoke("--check"), 0)
        self.assertFalse(any(m.endswith("/consume") for m, _ in self.calls))

    def test_no_early_or_late_execution(self):
        self.assertEqual(self.invoke("--execute", 999), 2)
        self.assertEqual(self.invoke("--execute", 1100), 2)
        self.assertEqual(self.calls, [])

    def test_one_credit_and_rerun_is_noop(self):
        self.assertEqual(self.invoke("--execute"), 0)
        self.assertEqual(self.invoke("--execute"), 0)
        consumes = [p for m, p in self.calls if m.endswith("/consume")]
        self.assertEqual(consumes, [{"creditId": "selected", "idempotencyKey": "stable-logical-attempt"}])

    def test_lost_response_reuses_credit_and_idempotency(self):
        self.lost_response = True
        self.assertEqual(self.invoke("--execute"), 0)
        consumes = [p for m, p in self.calls if m.endswith("/consume")]
        self.assertEqual(len(consumes), 2)
        self.assertEqual(consumes[0], consumes[1])
        self.assertEqual(consumes[0]["creditId"], "selected")


if __name__ == "__main__":
    unittest.main()
