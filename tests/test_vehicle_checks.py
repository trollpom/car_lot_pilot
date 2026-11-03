"""Synthetic contracts, safety gates, persistence and admin API. No external requests."""
import json
import os
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="vehicle-checks-tests-")
if "app.config" not in sys.modules:
    os.environ.update(EFRSB_DATA=TEMP.name,EFRSB_DB=str(Path(TEMP.name)/"demo.db"),
                      EMAIL_AUTO_SEND="0",EMAIL_AUTO_CHECK="0",TORGI_AUTO_SCRAPE="0",EMAIL2_ENABLED="0",
                      ADMIN_PASSWORD="history-test-only",DEEPSEEK_API_KEY="")

from fastapi.testclient import TestClient
from app import db
from app.api.main import app
from app.config import settings
from app.vehicle_checks import client as bridge, service, store
from app.vehicle_checks.models import Report
from scripts.configure_vehicle_checks import configure

VIN="XTA219050M0123456"
CONFIG={"CHECK_CARS_BASE_URL":"http://127.0.0.1:3000","CHECK_CARS_INTEGRATION_TOKEN":"integration-test-only-not-a-production-token",
        "CHECK_CARS_ALLOW_PAID":"0","CHECK_CARS_DEMO_ONLY":"0","CHECK_CARS_DAILY_LIMIT":"10"}


def report(mode="demo"):
    now=datetime.now(timezone.utc)
    return dict(contract="car-lot-pilot/v1",id="r-test-only",created_at=now.isoformat(),expires_at=(now+timedelta(hours=6)).isoformat(),
                mode=mode,demo=mode=="demo",vehicle=dict(vin=VIN,plate="не указан",make="Lada",model="Vesta",year=2021),
                summary=dict(score=85,level="low",verdict="Тестовая сводка",headline="Синтетическая фикстура",confidence="partial",factors=[]),
                coverage=dict(available=0,total=1,blocks=[dict(id="registration",title="Регистрация",status="unavailable",note="Недоступно",sources=["gibdd"])]),
                sources=[],facts=[dict(id="registration",title="Владельцев",status="unavailable",value=None)],limitations=["Тестовые данные"])


class VehicleChecksTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,CONFIG); self.env.start(); self.addCleanup(self.env.stop)
        self.network=patch("requests.sessions.Session.request",side_effect=AssertionError("External network forbidden in unit tests"))
        self.network.start(); self.addCleanup(self.network.stop)
        with store.connection() as conn: conn.execute("DELETE FROM lot_vehicle_checks")
        self.lot_id,_=db.upsert_lot(dict(efrsb_id="check-test-"+uuid.uuid4().hex,title="Синтетический лот",make="Lada",model="Vesta",year=2021,vin=VIN))
        self.client=TestClient(app)
        token=self.client.post("/api/admin/login",json={"password":settings.admin_password}).json()["token"]
        self.headers={"X-Admin-Token":token}
        self.url=f"/api/lots/{self.lot_id}/vehicle-check"

    def payload(self,**changes):
        return {"request_id":uuid.uuid4().hex,"query":VIN,"mode":"demo",**changes}

    def post(self,payload): return self.client.post(self.url,json=payload,headers=self.headers)

    def test_admin_required_for_status_history_and_start(self):
        for response in [self.client.get("/api/vehicle-checks/status"),self.client.get(self.url),self.client.post(self.url,json=self.payload())]:
            self.assertEqual(response.status_code,401)

    def test_disabled_configuration_never_calls_source(self):
        with patch.dict(os.environ,{"CHECK_CARS_BASE_URL":"","CHECK_CARS_INTEGRATION_TOKEN":""}),patch.object(service,"fetch_report") as fetch:
            self.assertEqual(self.post(self.payload()).status_code,503)
            self.assertFalse(self.client.get("/api/vehicle-checks/status",headers=self.headers).json()["configured"])
            fetch.assert_not_called()

    def test_live_and_paid_consents_are_required_before_call(self):
        with patch.object(service,"fetch_report") as fetch:
            self.assertEqual(self.post(self.payload(mode="live")).status_code,422)
            self.assertEqual(self.post(self.payload(mode="live",consent_to_external=True,allow_paid=True)).status_code,422)
            fetch.assert_not_called()
        with patch.object(service,"fetch_report",return_value=(Report.model_validate(report("live")),False)) as fetch:
            response=self.post(self.payload(mode="live",consent_to_external=True))
            self.assertEqual(response.status_code,200)
            self.assertFalse(fetch.call_args.args[1]["allow_paid"])

    def test_demo_cache_idempotency_and_mode_separation(self):
        p=self.payload()
        with patch.object(service,"fetch_report",side_effect=lambda c,p:(Report.model_validate(report(p["mode"])),False)) as fetch:
            first=self.post(p).json(); second=self.post(p).json()
            cached_payload=self.payload(); cached=self.post(cached_payload).json()
            self.assertEqual(second["reuse"],"idempotent"); self.assertEqual(cached["reuse"],"cache")
            self.assertEqual(first["check"]["id"],cached["check"]["id"])
            self.assertEqual(fetch.call_count,1)
            live=self.post(self.payload(mode="live",consent_to_external=True)).json()
            self.assertEqual(fetch.call_count,2); self.assertFalse(live["check"]["report"]["demo"])
            self.assertIsNone(live["check"]["report"]["summary"]["score"])
            self.assertEqual(live["check"]["report"]["summary"]["level"],"unknown")
            # Cache aliases retain idempotency after expiry rather than starting a new paid request.
            with store.connection() as conn:
                conn.execute("UPDATE lot_vehicle_checks SET expires_at=? WHERE id=?",((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),first["check"]["id"]))
            expired=self.post(cached_payload).json()
            self.assertEqual(expired["reuse"],"idempotent"); self.assertIsNone(expired["check"]["report"])
            self.assertEqual(fetch.call_count,2)

    def test_lot_identity_and_wrong_response_vin(self):
        with patch.object(service,"fetch_report") as fetch:
            self.assertEqual(self.post(self.payload(query="Z94K241BBLR123456")).status_code,422)
            fetch.assert_not_called()
        wrong=report(); wrong["vehicle"]["vin"]="Z94K241BBLR123456"
        with patch.object(service,"fetch_report",return_value=(Report.model_validate(wrong),False)):
            self.assertEqual(self.post(self.payload()).status_code,502)
        latest=self.client.get(self.url,headers=self.headers).json()["check"]
        self.assertEqual(latest["status"],"error"); self.assertIsNone(latest["report"])

    def test_expired_report_and_demo_only(self):
        old=report(); old["created_at"]=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(); old["expires_at"]=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with patch.object(service,"fetch_report",return_value=(Report.model_validate(old),False)):
            self.assertEqual(self.post(self.payload()).status_code,502)
        with patch.dict(os.environ,{"CHECK_CARS_DEMO_ONLY":"1"}),patch.object(service,"fetch_report") as fetch:
            self.assertEqual(self.post(self.payload(mode="live",consent_to_external=True)).status_code,422); fetch.assert_not_called()

    def test_daily_limit_errors_and_no_automatic_retry(self):
        p=self.payload(mode="live",consent_to_external=True)
        with patch.dict(os.environ,{"CHECK_CARS_DAILY_LIMIT":"1"}),patch.object(service,"fetch_report",side_effect=bridge.CheckError("timeout","Ответ не получен")) as fetch:
            self.assertEqual(self.post(p).status_code,503)
            retry=self.post(p).json(); self.assertEqual(retry["reuse"],"idempotent"); self.assertEqual(retry["check"]["status"],"error")
            # Same VIN with a plate is a different check, without using a different lot.
            self.assertEqual(self.post(self.payload(mode="live",consent_to_external=True,plate="А123ВС35")).status_code,429)
            self.assertEqual(fetch.call_count,1)

    def test_concurrent_reservations_and_recovery(self):
        def reserve(i):
            try: return store.reserve(self.lot_id,"request-"+str(i),"hash-"+str(i),"live",10)[1]
            except bridge.CheckError as e: return e.code
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(reserve,[1,2]))
        self.assertEqual(sorted(results),["busy","new"])
        store.init_store(recover=True)
        self.assertEqual(store.latest(self.lot_id)["status"],"error")

    def test_identifier_normalization_and_config_url(self):
        self.assertEqual(service.normalized(" xta-219050m0123456 "),(VIN,"vin"))
        self.assertEqual(service.normalized("a123bc35"),("А123ВС35","plate"))
        for bad in ["http://user:password@localhost:3000","http://169.254.169.254","http://example.com","https://example.com/?key=x","file:///tmp/data"]:
            with self.subTest(url=bad),self.assertRaises(bridge.CheckError): bridge.CheckSettings(bad,CONFIG["CHECK_CARS_INTEGRATION_TOKEN"]).validate()
        self.assertNotIn(CONFIG["CHECK_CARS_INTEGRATION_TOKEN"],json.dumps(bridge.CheckSettings.from_env().public_status()))

    def test_report_projection_rejects_mode_and_strips_extra_data(self):
        payload=report(); payload["owners"]=[{"private":"must not be saved"}]
        self.assertNotIn("owners",Report.model_validate(payload).model_dump())
        payload["demo"]=False
        with self.assertRaises(ValueError): Report.model_validate(payload)

    def test_http_client_validates_contract_and_redacts_errors(self):
        class Response:
            status_code=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def iter_content(self,size): yield json.dumps({"ok":True,"cached":False,"report":report()}).encode()
        with patch.object(bridge.requests,"post",return_value=Response()) as post:
            value,cached=bridge.fetch_report(bridge.CheckSettings.from_env(),{"query":VIN,"mode":"demo"})
            self.assertTrue(value.demo); self.assertFalse(cached)
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
        Response.status_code=500
        with patch.object(bridge.requests,"post",return_value=Response()),self.assertRaises(bridge.CheckError) as caught:
            bridge.fetch_report(bridge.CheckSettings.from_env(),{"query":VIN,"mode":"demo"})
        self.assertNotIn(CONFIG["CHECK_CARS_INTEGRATION_TOKEN"],str(caught.exception))

    def test_main_api_does_not_expose_history_in_public_lot(self):
        with patch.object(service,"fetch_report",return_value=(Report.model_validate(report()),False)):
            self.assertEqual(self.post(self.payload()).status_code,200)
        public=self.client.get(f"/api/lots/{self.lot_id}").json()
        self.assertNotIn("vehicle_check",public); self.assertNotIn("summary",public)
        self.assertEqual(self.client.get("/api/lots/9999999/vehicle-check",headers=self.headers).status_code,404)


class ConnectionSetupTests(unittest.TestCase):
    def test_setup_preserves_mail_and_provider_values_and_reuses_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); main=root/"main"; source=root/"check_cars"; main.mkdir(); source.mkdir()
            endpoint=source/"app/api/integrations/car-lot-pilot/route.ts"; endpoint.parent.mkdir(parents=True); endpoint.write_text("test")
            (main/".env").write_text("EMAIL_AUTO_SEND=0\nDEEPSEEK_API_KEY=example-placeholder\n")
            (source/".env.local").write_text("APIPOINT_TOKEN=example-placeholder\n")
            configure(main,source)
            first=(main/".env").read_text(); second=(source/".env.local").read_text()
            self.assertTrue(first.startswith("EMAIL_AUTO_SEND=0\nDEEPSEEK_API_KEY=example-placeholder\n"))
            self.assertTrue(second.startswith("APIPOINT_TOKEN=example-placeholder\n"))
            configure(main,source); self.assertEqual(first,(main/".env").read_text())
            token=[line for line in first.splitlines() if line.startswith("CHECK_CARS_INTEGRATION_TOKEN=")][0]
            self.assertIn(token,second)

    def test_mismatched_existing_tokens_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); endpoint=root/"source/app/api/integrations/car-lot-pilot/route.ts"; endpoint.parent.mkdir(parents=True); endpoint.write_text("test")
            main=root/"main"; main.mkdir()
            (main/".env").write_text("CHECK_CARS_INTEGRATION_TOKEN="+"a"*40+"\n")
            (root/"source/.env.local").write_text("CHECK_CARS_INTEGRATION_TOKEN="+"b"*40+"\n")
            with self.assertRaises(ValueError): configure(main,root/"source")
            self.assertIn("a"*40,(main/".env").read_text())


if __name__=="__main__": unittest.main()
