"""Seal a local EXPERIMENTAL candidate. There is no production promotion action."""
import argparse
from datetime import date,timedelta
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sales_coach.repositories.sales_v2_local_repository import LocalSalesV2Repository
from sales_coach.services.sales_v2_capture import atomic_json,now
from sales_coach.domain.sales_v2 import canonical,digest


def finalize(root,report_path):
    root=Path(root).expanduser().resolve();report=json.loads(Path(report_path).read_text());repo=LocalSalesV2Repository(root)
    if report['completed_days']!=60:raise ValueError('All 60 source days must be complete')
    if not all(d.get('source_document_count_verified') for d in report['days']):
        raise ValueError('Source document counts must reconcile with reported pagination')
    if not report.get('unit_audit'):raise ValueError('Unit audit must be completed before sealing the pilot')
    preflight=json.loads((root/'preflight.json').read_text());start=date.fromisoformat(preflight['start'])
    baseline_meta=json.loads((root/'baseline-meta.json').read_text())
    if hashlib.sha256((root/'baseline.jsonl.gz').read_bytes()).hexdigest()!=baseline_meta['sha256']:
        raise ValueError('Baseline archive changed')
    rows=[];load_ids=[];release=uuid4().hex
    for i in range(60):
        day=str(start+timedelta(days=i));source=json.loads((root/'days'/(day+'.json')).read_text());selected=json.loads((root/'selected'/(day+'.json')).read_text())
        if source['status']!='source_verified' or selected['capture_id']!=source['capture_id']:
            raise ValueError('Incomplete or stale source reference')
        if not re.fullmatch('[0-9a-f]{32}',selected['load_id']):raise ValueError('Invalid load id')
        recon=json.loads((root/'reconciliation'/(day+'.json')).read_text())
        rows.append({'release_id':release,'date':day,'load_id':selected['load_id'],'capture_id':source['capture_id'],
                     'source_hash':selected['source_digest'],'rows':selected['rows'],'included':selected['included'],
                     'v1_consistent':int(recon['consistent']),'status':'EXPERIMENTAL_NOT_OFFICIAL'})
        load_ids.append(selected['load_id'])
    with (root/'storage.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        repo.initialize()
        selection=','.join("'"+x+"'" for x in load_ids)
        counts=json.loads(repo.query(f"SELECT count() n,uniqExact(tuple(load_id,page,ordinal)) identities FROM fact_sales_lines_v2 WHERE load_id IN ({selection}) FORMAT JSONEachRow"))
        if int(counts['n'])!=sum(r['rows'] for r in rows) or counts['n']!=counts['identities']:
            raise ValueError('Candidate contains missing or duplicated observations')
        repo.query('CREATE TABLE IF NOT EXISTS sales_release_days_v2 (release_id String,date Date,load_id String,capture_id String,source_hash String,rows UInt64,included UInt64,v1_consistent UInt8,status String) ENGINE=MergeTree ORDER BY (release_id,date)')
        repo.query('CREATE TABLE IF NOT EXISTS sales_reconciliation_v2 (release_id String,date Date,consistent UInt8,result_json String CODEC(ZSTD)) ENGINE=MergeTree ORDER BY (release_id,date)')
        manifest=root/'releases'/release;manifest.mkdir(parents=True)
        daily=manifest/'days.jsonl';daily.write_text('\n'.join(canonical(r) for r in rows)+'\n')
        reconfile=manifest/'reconciliation.jsonl'
        with reconfile.open('w') as f:
            for r in rows:
                body=(root/'reconciliation'/(r['date']+'.json')).read_text()
                f.write(canonical({'release_id':release,'date':r['date'],'consistent':r['v1_consistent'],'result_json':body})+'\n')
        # Fixed local tables, inputs reside beneath the private pilot root.
        for table,path in [('sales_release_days_v2',daily),('sales_reconciliation_v2',reconfile)]:
            quoted=str(path).replace("\\","\\\\").replace("'","\\'")
            repo.query(f"INSERT INTO {table} FROM INFILE '{quoted}' FORMAT JSONEachRow")
        n=repo.query(f"SELECT count(),uniqExact(date) FROM sales_release_days_v2 WHERE release_id='{release}'").strip()
        if n!='60\t60':raise ValueError('Incomplete candidate manifest')
        result={'release_id':release,'created_at':now(),'status':'EXPERIMENTAL_NOT_OFFICIAL','official':False,
            'days':60,'source_and_storage_verified':True,'commercial_certified':report['reconciliation']['consistent'],
            'manifest_sha256':digest(rows),'baseline_sha256':baseline_meta['sha256'],'rows':sum(r['rows'] for r in rows),
            'included':sum(r['included'] for r in rows),'production_promoted':False}
        atomic_json(manifest/'release.json',result)
        atomic_json(root/'candidate.json',result)
        print(canonical(result))
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--report',required=True,type=Path)
    a=p.parse_args();finalize(a.root,a.report)
