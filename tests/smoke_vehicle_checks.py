"""Actual Python -> Next.js -> synthetic report round trip, never a paid/live request.

Run after npm ci && npm run build in check_cars:
  python tests/smoke_vehicle_checks.py ../check_cars
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import requests


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url, process, headers=None):
    deadline=time.monotonic()+50
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError("Test server stopped before becoming ready")
        try:
            response=requests.get(url,headers=headers,timeout=1)
            if response.status_code==200: return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError("Test server did not become ready")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_cars_dir",type=Path)
    args=parser.parse_args()
    source=args.check_cars_dir.resolve()
    next_cli=source/"node_modules/next/dist/bin/next"
    if not next_cli.is_file() or not (source/".next/BUILD_ID").is_file():
        parser.error("Run npm ci and npm run build in check_cars first")
    node=shutil.which("node")
    if not node: parser.error("Node.js is required")
    processes=[]
    with tempfile.TemporaryDirectory(prefix="car-checks-smoke-") as folder:
        temp=Path(folder); app_dir=temp/"main"; app_dir.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1]/"app",app_dir/"app",ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
        token=secrets.token_urlsafe(32)
        source_port,main_port=free_port(),free_port()
        source_url=f"http://127.0.0.1:{source_port}"; main_url=f"http://127.0.0.1:{main_port}"
        env=dict(os.environ)
        for key in list(env):
            if key.startswith(("CHECK_CARS_","EMAIL_","EMAIL2_","EFRSB_","TORGI_","AI_ANALYSIS_")): env.pop(key)
        env.update(CHECK_CARS_INTEGRATION_TOKEN=token,CHASSIS_DATA_DIR=str(temp/"chassis-data"),
                   APIPOINT_TOKEN="",APIPOINT_API_KEY="",DATA_MODE="demo",DISABLE_NHTSA="1",DISABLE_ROSSTANDART="1",DISABLE_TAXI="1",
                   NEXT_TELEMETRY_DISABLED="1",DEEPSEEK_API_KEY="",EMAIL_AUTO_SEND="0",EMAIL_AUTO_CHECK="0",EMAIL2_ENABLED="0",TORGI_AUTO_SCRAPE="0",
                   EFRSB_DATA=str(temp/"main-data"),EFRSB_DB=str(temp/"main-data/demo.db"),ADMIN_PASSWORD="demo-integration-only",
                   CHECK_CARS_BASE_URL=source_url,CHECK_CARS_DEMO_ONLY="1",CHECK_CARS_ALLOW_PAID="0")
        with (temp/"servers.log").open("w",encoding="utf-8") as log:
            try:
                source_process=subprocess.Popen([node,str(next_cli),"start","--hostname","127.0.0.1","--port",str(source_port)],cwd=source,env=env,stdout=log,stderr=log)
                processes.append(source_process)
                wait_ready(source_url+"/api/integrations/car-lot-pilot",source_process,{"Authorization":"Bearer "+token})
                seed="from app import db; db.init_db(); db.upsert_lot(dict(efrsb_id='integration-demo',title='Синтетический лот Lada Vesta',make='Lada',model='Vesta',year=2021,vin='XTA219050M0123456',category='Легковые автомобили',description='ДЕМО. Вымышленный лот; не реальное имущество.'))"
                subprocess.run([sys.executable,"-c",seed],cwd=app_dir,env=env,check=True,stdout=log,stderr=log)
                main_process=subprocess.Popen([sys.executable,"-m","uvicorn","app.api.main:app","--host","127.0.0.1","--port",str(main_port)],cwd=app_dir,env=env,stdout=log,stderr=log)
                processes.append(main_process); wait_ready(main_url+"/api/stats",main_process)
                login=requests.post(main_url+"/api/admin/login",json={"password":"demo-integration-only"},timeout=5); login.raise_for_status()
                headers={"X-Admin-Token":login.json()["token"]}
                lots=requests.get(main_url+"/api/lots",timeout=5).json()
                lot_id=lots["items"][0]["id"]
                url=main_url+f"/api/lots/{lot_id}/vehicle-check"
                assert requests.get(url,timeout=5).status_code==401
                assert requests.post(url,headers=headers,json={"query":"XTA219050M0123456","mode":"live","consent_to_external":True,"request_id":"live-blocked-test"},timeout=5).status_code==422
                payload={"query":"XTA219050M0123456","mode":"demo","request_id":"synthetic-round-trip"}
                response=requests.post(url,headers=headers,json=payload,timeout=30); response.raise_for_status()
                data=response.json(); report=data["check"]["report"]
                assert report["demo"] is True and report["mode"]=="demo"
                assert report["vehicle"]["vin"]==payload["query"] and report["coverage"]["blocks"]
                payload["request_id"]="cached-round-trip"
                cached=requests.post(url,headers=headers,json=payload,timeout=5).json()
                assert cached["reuse"]=="cache" and cached["check"]["id"]==data["check"]["id"]
                saved=requests.get(url,headers=headers,timeout=5).json()
                assert saved["check"]["report"]["id"]==report["id"]
                assert requests.get(main_url+"/vehicle-checks.js",timeout=5).status_code==200
                original=requests.post(main_url+f"/api/lots/{lot_id}/analysis/calculate",headers=headers,json={},timeout=5)
                original.raise_for_status()
                assert original.json()["economics"]["decision"]=="insufficient_data"
                assert requests.get(source_url+"/api/health",timeout=5).json()["reports"]==1
                print(json.dumps({"result":"PASS","round_trip":"car_lot_pilot -> check_cars -> validated demo report", "mode":report["mode"],"blocks":report["coverage"]["total"],"repeat":"local cache","admin_auth":"PASS","live_on_demo":"blocked","existing_offline_analysis":"PASS","paid_requests":0},ensure_ascii=False))
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=8)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()


if __name__=="__main__": main()
