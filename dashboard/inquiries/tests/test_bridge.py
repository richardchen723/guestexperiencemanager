"""Run the actual CLI bridge against the actual local CRM API."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from werkzeug.serving import make_server

from dashboard.auth.api_keys import create_api_key
from .test_api import app, client
from .test_email_adapter import message


def test_packet_hash_validation_and_retry_safe_bridge(app,factory,tmp_path):
    key=create_api_key('QA Larry bridge',app.qa_users['admin'])
    raw=message()
    raw['payload']['headers'][0]['value']='Enchanted Havens <yunhang.chen@gmail.com>'
    input_path=tmp_path/'gmail.json';input_path.write_text(json.dumps({'messages':[raw]}))
    packet_path=tmp_path/'packet.json';results_path=tmp_path/'results.json'
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    script=Path(__file__).resolve().parents[3]/'scripts'/'inquiry_crm_client.py'
    env={**os.environ,'INQUIRY_CRM_API_KEY':key}
    def run(*args):
        return subprocess.run([sys.executable,str(script),'--base-url',origin,*args],env=env,capture_output=True,text=True,timeout=30)
    try:
        prepared=run('prepare','--gmail-file',str(input_path),'--output',str(packet_path))
        assert prepared.returncode==0,prepared.stderr
        assert packet_path.stat().st_mode & 0o777 == 0o600
        packet=json.loads(packet_path.read_text());item=packet['messages'][0]
        results_path.write_text(json.dumps({'results':[{'message_id':item['email']['message_id'],'source_hash':item['source_hash'],
                                      'extraction':{'reference':'EH-12345678','fields':{'guest_name':'Sally'}}}]}))
        for _ in range(2):
            ingested=run('ingest','--packet',str(packet_path),'--results',str(results_path))
            assert ingested.returncode==0,ingested.stderr
        c=client(app);lead=c.get('/inquiries/api/leads').json['leads'][0]
        detail=c.get(f'/inquiries/api/leads/{lead["id"]}').json
        assert len(detail['messages'])==1 and len(detail['activities'])==1
        item['email']['body']='Changed original'
        packet_path.write_text(json.dumps(packet))
        rejected=run('ingest','--packet',str(packet_path),'--results',str(results_path))
        assert rejected.returncode!=0 and 'hash mismatch' in rejected.stderr
    finally:
        server.shutdown();thread.join(timeout=5)
