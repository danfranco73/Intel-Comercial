"""Read-only source preflight and immutable 60-day baseline; private local files only."""
import argparse
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clickhouse_client import get_clickhouse_client,_qualified_table
from mongo_client import get_db
from sales_coach.services.sales_coverage_service import SalesCoverageService
from sales_coach.services.sales_v2_capture import atomic_json,now


def prepare(root,snapshot_id):
    root=Path(root).expanduser().resolve();root.mkdir(parents=True,exist_ok=True);root.chmod(0o700)
    if (root/'preflight.json').exists() or (root/'baseline.jsonl.gz').exists():
        raise ValueError('Pilot baseline already exists; resume it, never overwrite it')
    c=get_clickhouse_client();db=get_db();table=_qualified_table()
    yesterday=datetime.now(ZoneInfo('America/Argentina/Cordoba')).date()-timedelta(days=1)
    end=min(yesterday,c.query(f'SELECT max(date) FROM {table}').result_rows[0][0]);start=end-timedelta(days=59)
    coverage=SalesCoverageService(db).assess('clickhouse',{'pilot':(start,end)})
    if coverage['pilot']['status']!='complete':raise ValueError('Latest 60 closed days are not certified')
    database,tab=table.split('.')
    def mutations():
        return c.query('SELECT count() FROM system.mutations WHERE database={db:String} AND table={table:String} AND NOT is_done',parameters={'db':database,'table':tab}).result_rows[0][0]
    if mutations():raise ValueError('Baseline blocked by active source mutations')
    params={'start':str(start),'end':str(end)};tick=time.monotonic()
    response=c.query(f'SELECT * FROM {table} WHERE date BETWEEN {{start:Date}} AND {{end:Date}} ORDER BY date,client_key,invoice,product_key,seller_key,sales_scheme_key,route_description,channel',parameters=params)
    if mutations() or len(response.result_rows)!=coverage['pilot']['observed_rows']:
        raise ValueError('Baseline changed relative to completed ingestion evidence')
    with gzip.open(root/'baseline.jsonl.gz','wt') as f:
        for row in response.result_rows:f.write(json.dumps(dict(zip(response.column_names,row)),ensure_ascii=False,default=str)+'\n')
    atomic_json(root/'baseline-meta.json',{'rows':len(response.result_rows),'seconds':time.monotonic()-tick,'captured_at':now(),
        'sha256':hashlib.sha256((root/'baseline.jsonl.gz').read_bytes()).hexdigest(),'columns':response.column_names,'source_table':table})
    snapshot=db.intelligence_stock_snapshots.find_one({'snapshot_id':snapshot_id},{'_id':0})
    if not snapshot or snapshot['status']!='complete':raise ValueError('A complete reference stock snapshot is required')
    atomic_json(root/'stock-reference.json',snapshot)
    for col in ('intelligence_stock_rows','erp_articles','intelligence_product_identities'):
        q={'snapshot_id':snapshot_id} if col=='intelligence_stock_rows' else {'version':snapshot['identity_version']} if col=='intelligence_product_identities' else {}
        with gzip.open(root/(col+'.jsonl.gz'),'wt') as f:
            for row in db[col].find(q,{'_id':0}):f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
    atomic_json(root/'preflight.json',{**params,'coverage':coverage,'prepared_at':now(),'production_writes':False})
    print(json.dumps(params))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--snapshot-id',required=True)
    a=p.parse_args();prepare(a.root,a.snapshot_id)
