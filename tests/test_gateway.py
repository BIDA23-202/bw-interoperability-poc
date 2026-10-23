import base64
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

import multiroute_gateway as gw
from registry import capability_matrix

BASE = "/api/demo/sessions"


def basic(password, user="demo"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class GatewayBase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(gw.app)
        gw.SESSIONS.clear()
        patcher = mock.patch.object(gw, "DEMO_PASSWORD", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(gw.SESSIONS.clear)

    def new_session(self):
        r = self.client.post(BASE)
        self.assertEqual(r.status_code, 201)
        return r.json()["session_id"]

    def quote(self, sid, **override):
        body = {
            "source": "ABSA_BW", "destination": "ORANGE_MONEY",
            "amount": "125.00", "fee_policy": "NONE",
        }
        body.update(override)
        return self.client.post(f"{BASE}/{sid}/quotes", json=body)

    def transfer(self, sid, quote_id, **override):
        body = {
            "source": "ABSA_BW", "destination": "ORANGE_MONEY",
            "reference": "GW-001", "amount": "125.00",
            "quote_id": quote_id, "scenario": "SUCCESS",
        }
        body.update(override)
        return self.client.post(f"{BASE}/{sid}/transfers", json=body)

    def assert_client_error(self, response):
        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)
        self.assertIn("detail", response.json())


class HealthAndSessionTests(GatewayBase):
    def test_health_reports_simulation_only(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mode"], "SIMULATION ONLY")

    def test_create_session_returns_201_and_reference_data(self):
        r = self.client.post(BASE)
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertTrue(data["session_id"])
        self.assertEqual(len(data["providers"]), 10)
        self.assertEqual(len(data["capabilities"]), len(capability_matrix()))
        self.assertIn("EXPERIMENTAL", data["fee_policies"])

    def test_session_ids_are_unique(self):
        self.assertNotEqual(self.new_session(), self.new_session())

    def test_overview_can_be_read_back(self):
        sid = self.new_session()
        self.assertEqual(self.client.get(f"{BASE}/{sid}").status_code, 200)

    def test_unknown_session_is_a_client_error(self):
        self.assert_client_error(self.client.get(f"{BASE}/does-not-exist"))

    def test_capacity_limit_returns_503(self):
        with mock.patch.object(gw, "MAX_SESSIONS", 1):
            self.new_session()
            r = self.client.post(BASE)
        self.assertEqual(r.status_code, 503)

    def test_expired_sessions_are_pruned_on_next_create(self):
        with mock.patch.object(gw, "MAX_SESSIONS", 1), \
                mock.patch.object(gw, "SESSION_TTL", -1):
            self.new_session()
            self.assertEqual(self.client.post(BASE).status_code, 201)


class TransferFlowTests(GatewayBase):
    def assert_status(self, response, code):
        self.assertEqual(response.status_code, code, response.text)
        self.assertIn("detail", response.json())

    def test_quote_then_transfer_then_read_and_advance(self):
        sid = self.new_session()
        q = self.quote(sid)
        self.assertEqual(q.status_code, 200)
        quote_id = q.json()["quote_id"]

        created = self.transfer(sid, quote_id)
        self.assertEqual(created.status_code, 200)
        transfer_id = created.json()["transfer"]["id"]

        self.assertEqual(
            self.client.get(f"{BASE}/{sid}/transfers/{transfer_id}").status_code, 200
        )
        self.assertEqual(
            self.client.post(f"{BASE}/{sid}/transfers/{transfer_id}/advance").status_code,
            200,
        )

    def test_experimental_policy_quote_is_available_over_http(self):
        sid = self.new_session()
        r = self.quote(sid, fee_policy="EXPERIMENTAL")
        self.assertEqual(r.status_code, 200)
        self.assertIn("quote_id", r.json())

    def test_quote_cannot_be_reused_over_http(self):
        sid = self.new_session()
        quote_id = self.quote(sid).json()["quote_id"]
        self.assertEqual(self.transfer(sid, quote_id).status_code, 200)
        self.assert_status(self.transfer(sid, quote_id, reference="GW-002"), 409)

    def test_unknown_quote_is_409(self):
        sid = self.new_session()
        self.assert_status(self.transfer(sid, "no-such-quote"), 409)

    def test_quote_from_another_session_is_409(self):
        a, b = self.new_session(), self.new_session()
        quote_id = self.quote(a).json()["quote_id"]
        self.assert_status(self.transfer(b, quote_id), 409)

    def test_quote_must_match_instruction_is_409(self):
        sid = self.new_session()
        quote_id = self.quote(sid, amount="125.00").json()["quote_id"]
        self.assert_status(self.transfer(sid, quote_id, amount="126.00"), 409)

    def test_unknown_fee_policy_is_400(self):
        sid = self.new_session()
        self.assert_status(self.quote(sid, fee_policy="MADE_UP"), 400)

    def test_invalid_amount_is_400(self):
        sid = self.new_session()
        self.assert_status(self.quote(sid, amount="abc"), 400)

    def test_unknown_transfer_is_404(self):
        sid = self.new_session()
        self.assert_status(self.client.get(f"{BASE}/{sid}/transfers/nope"), 404)

    def advance_until(self, sid, result, stop):
        for _ in range(20):
            t = result["transfer"]
            if t["terminal"] or t["status"] in stop:
                return result
            r = self.client.post(f"{BASE}/{sid}/transfers/{t['id']}/advance")
            self.assertEqual(r.status_code, 200, r.text)
            result = r.json()
        self.fail(f"Transfer never reached {stop}; last: {result['transfer']}")

    def pending_transfer(self, sid):
        quote_id = self.quote(sid).json()["quote_id"]
        created = self.transfer(sid, quote_id, scenario="PENDING")
        self.assertEqual(created.status_code, 200, created.text)
        result = self.advance_until(sid, created.json(), {"PENDING"})
        self.assertEqual(result["transfer"]["status"], "PENDING")
        return result["transfer"]["id"]

    def test_resolving_before_pending_is_409(self):
        sid = self.new_session()
        quote_id = self.quote(sid).json()["quote_id"]
        created = self.transfer(sid, quote_id, scenario="PENDING").json()
        if created["transfer"]["status"] == "PENDING":
            self.skipTest("create already returns PENDING")
        tid = created["transfer"]["id"]
        r = self.client.post(
            f"{BASE}/{sid}/transfers/{tid}/resolve", json={"action": "SUCCESS"}
        )
        self.assert_status(r, 409)

    def test_pending_transfer_resolves_to_settled_once(self):
        sid = self.new_session()
        tid = self.pending_transfer(sid)
        url = f"{BASE}/{sid}/transfers/{tid}/resolve"

        first = self.client.post(url, json={"action": "SUCCESS"})
        self.assertEqual(first.status_code, 200, first.text)
        final = self.advance_until(sid, first.json(), set())
        self.assertEqual(final["transfer"]["status"], "SETTLED")

        self.assert_status(self.client.post(url, json={"action": "SUCCESS"}), 409)

    def test_pending_transfer_failed_resolution_reverses(self):
        sid = self.new_session()
        tid = self.pending_transfer(sid)
        r = self.client.post(
            f"{BASE}/{sid}/transfers/{tid}/resolve", json={"action": "FAILED"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        final = self.advance_until(sid, r.json(), set())
        self.assertEqual(final["transfer"]["status"], "REVERSED")


class ValidationTests(GatewayBase):
    def test_transfer_without_quote_id_is_422(self):
        sid = self.new_session()
        body = {
            "source": "ABSA_BW", "destination": "ORANGE_MONEY",
            "reference": "GW-001", "amount": "125.00",
        }
        self.assertEqual(
            self.client.post(f"{BASE}/{sid}/transfers", json=body).status_code, 422
        )

    def test_empty_reference_is_422(self):
        sid = self.new_session()
        quote_id = self.quote(sid).json()["quote_id"]
        self.assertEqual(self.transfer(sid, quote_id, reference="").status_code, 422)

    def test_overlong_reference_is_422(self):
        sid = self.new_session()
        quote_id = self.quote(sid).json()["quote_id"]
        self.assertEqual(
            self.transfer(sid, quote_id, reference="X" * 81).status_code, 422
        )

    def test_unknown_scenario_is_422(self):
        sid = self.new_session()
        quote_id = self.quote(sid).json()["quote_id"]
        self.assertEqual(
            self.transfer(sid, quote_id, scenario="EXPLODE").status_code, 422
        )

    def test_unknown_resolution_action_is_422(self):
        sid = self.new_session()
        r = self.client.post(
            f"{BASE}/{sid}/transfers/whatever/resolve", json={"action": "BOGUS"}
        )
        self.assertEqual(r.status_code, 422)

    def test_quote_body_missing_fields_is_422(self):
        sid = self.new_session()
        r = self.client.post(f"{BASE}/{sid}/quotes", json={"source": "ABSA_BW"})
        self.assertEqual(r.status_code, 422)


class PasswordTests(GatewayBase):
    def with_password(self, value="s3cret"):
        patcher = mock.patch.object(gw, "DEMO_PASSWORD", value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_password_configured_means_open_access(self):
        self.assertEqual(self.client.post(BASE).status_code, 201)

    def test_missing_credentials_are_refused_with_challenge(self):
        self.with_password()
        r = self.client.post(BASE)
        self.assertEqual(r.status_code, 401)
        self.assertIn("Basic", r.headers["www-authenticate"])

    def test_wrong_password_is_refused(self):
        self.with_password()
        self.assertEqual(
            self.client.post(BASE, headers=basic("wrong")).status_code, 401
        )

    def test_malformed_header_is_refused(self):
        self.with_password()
        r = self.client.post(BASE, headers={"Authorization": "Basic !!!not-base64"})
        self.assertEqual(r.status_code, 401)

    def test_correct_password_is_accepted(self):
        self.with_password()
        self.assertEqual(
            self.client.post(BASE, headers=basic("s3cret")).status_code, 201
        )

    def test_username_is_ignored_only_password_matters(self):
        self.with_password()
        self.assertEqual(
            self.client.post(BASE, headers=basic("s3cret", user="anyone")).status_code,
            201,
        )

    def test_health_stays_open_without_credentials(self):
        self.with_password()
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_api_routes_are_protected_not_just_session_creation(self):
        self.with_password()
        self.assertEqual(self.client.get(f"{BASE}/abc").status_code, 401)


if __name__ == "__main__":
    unittest.main()