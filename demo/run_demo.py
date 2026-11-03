"""Run the unchanged application on localhost with disposable synthetic records."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8781)
    parser.add_argument('--live-ai',action='store_true',help='Explicitly allow one provider request with your configured key')
    parser.add_argument('--vehicle-checks',action='store_true',help='Connect the configured local check_cars service in synthetic demo mode only')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Use a port between 1024 and 65535')
    source=Path(__file__).resolve().parents[1]
    key=''
    checks={}
    if args.live_ai:
        from dotenv import dotenv_values
        key=os.environ.get('DEEPSEEK_API_KEY','').strip()
        if not key and (source/'.env').is_file():key=str(dotenv_values(source/'.env').get('DEEPSEEK_API_KEY') or '').strip()
        if not key:parser.error('Configure the key with python scripts/configure_deepseek.py; never paste it into chat or command arguments')
    if args.vehicle_checks:
        from dotenv import dotenv_values
        values=dotenv_values(source/'.env') if (source/'.env').is_file() else {}
        for name in ('CHECK_CARS_BASE_URL','CHECK_CARS_INTEGRATION_TOKEN'):
            checks[name]=os.environ.get(name) or values.get(name) or ''
        if not checks['CHECK_CARS_BASE_URL'] or len(checks['CHECK_CARS_INTEGRATION_TOKEN'])<32:
            parser.error('Configure the connection with python scripts/configure_vehicle_checks.py ../check_cars')
    with tempfile.TemporaryDirectory(prefix='car-lot-pilot-demo-') as folder:
        root=Path(folder)
        shutil.copytree(source/'app',root/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        # No mailbox settings, production paths or .env file enter the disposable app.
        for name in list(os.environ):
            if name.startswith(('EMAIL_','EMAIL2_','EFRSB_','TORGI_','AI_ANALYSIS_','CHECK_CARS_')):os.environ.pop(name,None)
        os.environ.update(EFRSB_DATA=str(root/'data'),EFRSB_DB=str(root/'data/demo.db'),EMAIL_AUTO_SEND='0',EMAIL_AUTO_CHECK='0',TORGI_AUTO_SCRAPE='0',EMAIL2_ENABLED='0',ADMIN_PASSWORD='demo-local-only',DEEPSEEK_API_KEY=key,AI_ANALYSIS_DAILY_LIMIT='1',AI_ANALYSIS_MAX_PHOTOS='1',AI_ANALYSIS_MAX_QUERIES='1',AI_ANALYSIS_MAX_PAGES='1',AI_ANALYSIS_MAX_TOKENS='1500')
        if checks:os.environ.update(checks,CHECK_CARS_DEMO_ONLY='1',CHECK_CARS_ALLOW_PAID='0')
        sys.path.insert(0,str(root))
        from app.api.main import app
        from app import db
        from fixtures import seed
        seed(db,root/'data/photos')
        print(f'DEMO http://127.0.0.1:{args.port} | admin: demo-local-only | live AI: {bool(key)}')
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=args.port,workers=1)


if __name__=='__main__':main()
