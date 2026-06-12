"""Unit tests for ``format_graph_error`` — the backend chokepoint that turns
Graph/requests/tenacity failures into one clean operator line (no raw
``{'error': {...}}`` dicts leaking to the demo UI or job ``error_message``).
"""

import json
import unittest
from unittest.mock import Mock

import requests
from tenacity import RetryError

from autopackager.utils.graph_client import format_graph_error


def _http_error(status, body):
    resp = Mock()
    resp.status_code = status
    resp.json = lambda: body
    resp.text = json.dumps(body)
    err = requests.HTTPError("boom")
    err.response = resp
    return err


class TestFormatGraphError(unittest.TestCase):
    def test_model_validation_failure_is_clean(self):
        err = _http_error(400, {"error": {
            "code": "ModelValidationFailure",
            "message": "The property 'autoUpdateSupersededApps' does not exist on type 'win32LobAppAutoUpdateSettings'.",
        }})
        out = format_graph_error(err, action="Deployment failed")
        self.assertTrue(out.startswith("Deployment failed: Intune rejected the request payload (400 ModelValidationFailure):"))
        self.assertNotIn("{", out)  # no raw dict
        self.assertIn("autoUpdateSupersededApps", out)

    def test_permission_403_names_missing_role(self):
        err = _http_error(403, {"error": {
            "code": "Authorization_RequestDenied",
            "message": "Insufficient privileges to complete the operation.",
        }})
        out = format_graph_error(err, action="Deployment failed")
        self.assertIn("Insufficient Graph permissions (403", out)
        self.assertIn("Group.ReadWrite.All", out)  # actionable ring-creation hint

    def test_unwraps_tenacity_retry_error(self):
        inner = _http_error(400, {"error": {"code": "ModelValidationFailure", "message": "bad prop"}})

        class _Attempt:
            def exception(self_inner):
                return inner

        out = format_graph_error(RetryError(_Attempt()), action="Deployment failed")
        self.assertIn("ModelValidationFailure", out)
        self.assertIn("bad prop", out)

    def test_unwraps_intune_nested_json_message(self):
        err = _http_error(400, {"error": {
            "code": "BadRequest",
            "message": json.dumps({"_version": 3, "Message": "Cannot delete this app as it is the child of another app: abc."}),
        }})
        out = format_graph_error(err)
        self.assertIn("Cannot delete this app", out)
        self.assertNotIn("_version", out)  # nested envelope stripped

    def test_429_and_404_labelled(self):
        self.assertIn("Throttled by Graph (429)", format_graph_error(_http_error(429, {"error": {"code": "TooManyRequests", "message": "slow down"}})))
        self.assertIn("not found (404)", format_graph_error(_http_error(404, {"error": {"code": "NotFound", "message": "gone"}})))

    def test_non_http_error_falls_back_to_type_and_message(self):
        out = format_graph_error(ValueError("something local broke"), action="Deployment failed")
        self.assertEqual(out, "Deployment failed: ValueError: something local broke")

    def test_never_raises_on_garbage_response(self):
        resp = Mock()
        resp.status_code = 500
        resp.json = Mock(side_effect=ValueError("not json"))
        resp.text = "Internal Server Error"
        err = requests.HTTPError("boom")
        err.response = resp
        out = format_graph_error(err)
        self.assertIn("HTTP 500", out)


if __name__ == "__main__":
    unittest.main()
