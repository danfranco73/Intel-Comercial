"""Capture article masters and a private stock observation, READ ONLY in Chess."""
import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from erp_client import _parse_page_info
from sales_coach.services.sales_v2_capture import Capture, atomic_json, now


def run(root):
    capture=Capture(root);root=capture.root
    # Share the capture lock: at most one pilot HTTP worker talks to Chess.
    with (root/'capture.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        attempt_id=uuid4().hex
        metadata={"started_at":now(),"attempt_id":attempt_id,"articles":[],"stock":[],"production_writes":False}
        for cancelled in ("false","true"):
            expected=None
            for page in range(1,501):
                payload,m,path=capture.get('/articulos/',{'nroLote':page,'anulado':cancelled},root/'auxiliary'/attempt_id/'articles'/cancelled/str(page))
                rows=payload.get('Articulos',{}).get('eArticulos')
                current,total=_parse_page_info(payload.get('cantArticulos'))
                if payload.get('error') or not isinstance(rows,list):
                    raise ValueError('Invalid article response; inspect archived evidence')
                if expected is not None and total!=expected:raise ValueError('Article pagination changed')
                expected=total
                metadata['articles'].append({**m,'path':path,'rows':len(rows),'page':page,'anulado':cancelled,'pagination':payload.get('cantArticulos')})
                atomic_json(root/'auxiliary.json',metadata)
                print('ARTICLES',cancelled,page,len(rows),flush=True)
                if total is not None and page>=total:break
                if total is None:raise ValueError('Article pagination unavailable')
            else:raise ValueError('Article pagination overflow')
        snapshot=json.loads((root/'stock-reference.json').read_text())
        day=datetime.now(ZoneInfo('America/Argentina/Cordoba')).date()
        metadata['stock_date']=str(day)
        for deposit in snapshot['expected_deposit_ids']:
            payload,m,path=capture.get('/stock/',{'idDeposito':deposit,'fechaStock':day.strftime('%d/%m/%Y'),'frescura':'true'},root/'auxiliary'/attempt_id/'stock'/deposit)
            rows=payload.get('dsStockFisicoApi',{}).get('dsStock')
            if not isinstance(rows,list) or payload.get('error'):raise ValueError('Invalid stock response')
            if any(str(r.get('idDeposito'))!=deposit for r in rows):raise ValueError('Stock deposit mismatch')
            metadata['stock'].append({**m,'path':path,'rows':len(rows),'deposit_id':deposit})
            atomic_json(root/'auxiliary.json',metadata)
            print('STOCK',deposit,len(rows),flush=True)
        metadata.update(finished_at=now(),status='complete',expected_deposits=snapshot['expected_deposit_ids'])
        atomic_json(root/'auxiliary.json',metadata)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path)
    run(p.parse_args().root)
