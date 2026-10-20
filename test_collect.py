import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import collect


class CollectorChecks(unittest.TestCase):
    def test_contract_and_failure_preservation(self):
        groups = [{"groupName": "경제", "keywords": ["환율"]}]
        body = collect.request_body(groups, date(2026, 1, 1))
        self.assertEqual((body["startDate"], body["endDate"]), ("2025-12-02", "2025-12-31"))
        raw = {**{k: body[k] for k in ("startDate", "endDate", "timeUnit")},
               "results": [{"title": "경제", "keywords": ["환율"],
                            "data": [{"period": "2025-12-31", "ratio": 100}]}]}
        self.assertEqual(collect.validate_response(raw, body), raw["results"])
        for invalid in (float("nan"), -1, 101, True):
            broken = copy.deepcopy(raw)
            broken["results"][0]["data"][0]["ratio"] = invalid
            with self.assertRaises(ValueError):
                collect.validate_response(broken, body)
        with self.assertRaises(ValueError):
            collect.request_body(groups * 6, date(2026, 1, 1))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "public").mkdir()
            (root / "keywords.json").write_text(json.dumps(groups), encoding="utf-8")
            latest = root / "public/latest.json"
            latest.write_text('{"collected_at":"previous-good-result"}', encoding="utf-8")
            original = latest.read_bytes()
            with patch.object(collect, "ROOT", root), patch.dict(collect.os.environ, {}, clear=True):
                self.assertEqual(collect.main(), 1)
            self.assertEqual(latest.read_bytes(), original)
            with patch.object(collect, "ROOT", root), patch.dict(
                collect.os.environ, {"NAVER_API_CLIENT_ID": "test-id", "NAVER_API_CLIENT_SECRET": "test-secret"}
            ), patch.object(collect, "urlopen", side_effect=HTTPError(collect.ENDPOINT, 401, "", {}, None)):
                self.assertEqual(collect.main(), 1)
            self.assertEqual(latest.read_bytes(), original)
            status = json.loads((root / "public/status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["error_code"], "http_401")
            self.assertNotIn("test-secret", json.dumps(status))


if __name__ == "__main__":
    unittest.main()
