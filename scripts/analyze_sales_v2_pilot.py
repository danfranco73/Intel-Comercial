"""Reconcile the local pilot and produce non-production logistics/unit evidence."""
import argparse
from collections import Counter,defaultdict
from datetime import date,timedelta
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clickhouse_client import _compact_records
from erp_client import normalize_erp_sale_row
from sales_coach.domain.sales_v2 import METRICS,canonical,digest,identifier,number,reconcile,unit_evidence
from sales_coach.services.sales_v2_capture import atomic_json,now

ZERO=Decimal(0)


def read_payload(root,page):
    body=gzip.open(root/page['path'],'rb').read()
    if hashlib.sha256(body).hexdigest()!=page['sha256']:raise ValueError('Raw checksum failed')
    return json.loads(body)


def analyze(root,output):
    tick=time.monotonic();root=Path(root).expanduser().resolve();output=Path(output);output.mkdir(parents=True,exist_ok=True)
    preflight=json.loads((root/'preflight.json').read_text());end=date.fromisoformat(preflight['end'])
    baseline=defaultdict(list)
    for text in gzip.open(root/'baseline.jsonl.gz','rt'):
        r=json.loads(text);baseline[r['date']].append(r)
    report={'generated_at':now(),'production':False,'promoted':False,'window':preflight,'days':[],
        'normalizer_version':'sales-v2-pilot-1','deposits':{},'unit_audit':{},'experimental':[]}
    all_v1=[];all_v2=[];products=defaultdict(list);pairs=defaultdict(lambda:defaultdict(lambda:ZERO));names={}
    identities=Counter();totals=Counter();deposits={};sheets=defaultdict(lambda:defaultdict(set));pair_stats=defaultdict(set)
    dimensional=defaultdict(lambda:{'rows':0,'net':ZERO,'quantity':ZERO,'products':set()})
    total_net=ZERO;covered_net=ZERO;absolute_net=ZERO;covered_absolute=ZERO
    raw_net=ZERO;raw_final=ZERO;unknown_keys=set();line_keys=Counter();doc_deposits=defaultdict(set)
    for checkpoint in sorted((root/'days').glob('*.json')):
        entry=json.loads(checkpoint.read_text());day=entry['day'];selected=root/'selected'/checkpoint.name
        if entry['status']!='source_verified' or not selected.exists():
            report['days'].append({'day':day,'status':entry['status']});continue
        load=json.loads(selected.read_text())
        if load['capture_id']!=entry['capture_id']:raise ValueError('Manifest references obsolete source attempt')
        records=[];excluded=Counter();physical_missing=0;physical_present=0;sheet_rows=0;allrows=0;rawdocs=set();reported_docs=set()
        for p in entry['passes'][0]['pages']:
            match=re.search(r'Cantidad de comprobantes totales:\s*(\d+)',str(p.get('pagination','')))
            if match:reported_docs.add(int(match.group(1)))
            for raw in read_payload(root,p)['dsReporteComprobantesApi'].get('VentasResumen',[]):
                rawdocs.add(canonical([raw.get(k) for k in ['idEmpresa','idDocumento','letra','serie','nrodoc']]))
                totals['raw_rows']+=1
                totals['raw_deposit_present']+=bool(identifier(raw.get('idDeposito')))
                allrows+=1;legacy=normalize_erp_sale_row(raw)
                if legacy is None:
                    reason='cancelled' if raw.get('anulado')=='SI' else 'invalid_commercial'
                    excluded[reason]+=1;totals['excluded_'+reason]+=1;continue
                records.append(legacy);totals['included_rows']+=1
                dep=identifier(raw.get('idDeposito'));physical=identifier(raw.get('idArticulo'));statistical=identifier(raw.get('idArticuloEstadistico'))
                amount=number(legacy['amount_net']) or ZERO;total_net+=amount;absolute_net+=abs(amount)
                raw_net+=number(raw.get('subtotalNeto')) or ZERO;raw_final+=number(raw.get('subtotalFinal')) or ZERO
                if dep:totals['deposit_present']+=1;covered_net+=amount;covered_absolute+=abs(amount)
                if physical:physical_present+=1;totals['physical_present']+=1
                else:physical_missing+=1
                if statistical:totals['statistical_present']+=1
                identities[(physical,statistical)]+=1
                depkey=dep or 'UNKNOWN'
                if depkey not in deposits:
                    deposits[depkey]={'rows':0,'amount_net':ZERO,'amount_final':ZERO,'quantity_commercial':ZERO,
                        'products':set(),'companies':set(),'forces':set(),'channels':set(),'sheets':set(),'sheet_rows':0}
                agg=deposits[depkey];agg['rows']+=1;agg['amount_net']+=amount;agg['amount_final']+=number(legacy['amount_final']) or ZERO
                agg['quantity_commercial']+=number(legacy['quantity']) or ZERO
                if physical:agg['products'].add(physical)
                agg['companies'].add((str(raw.get('idEmpresa')),str(raw.get('dsEmpresa'))))
                agg['forces'].add((str(raw.get('idFuerzaVentas')),str(raw.get('dsFuerzaVentas'))))
                agg['channels'].add((str(raw.get('idCanalMkt')),str(raw.get('dsCanalMkt')),str(raw.get('idSubcanalMkt')),str(raw.get('dsSubcanalMKT'))))
                g=dimensional[(depkey,str(raw.get('idEmpresa')),str(raw.get('idFuerzaVentas')),legacy['channel'])]
                g['rows']+=1;g['net']+=amount;g['quantity']+=number(legacy['quantity']) or ZERO
                if physical:g['products'].add(physical)
                sheet=raw.get('planillaCarga')
                if sheet:
                    agg['sheets'].add(sheet);agg['sheet_rows']+=1;sheet_rows+=1
                    for key,val in [('deposits',depkey),('companies',raw.get('idEmpresa')),('forces',raw.get('idFuerzaVentas')),('channels',legacy['channel'])]:sheets[sheet][key].add(val)
                if physical:
                    names[physical]=raw.get('dsArticulo')
                    products[physical].append({k:raw.get(k) for k in ['cantidadesTotal','cantidadSolicitada','unidadesSolicitadas','unimedtotal','presentacionArticulo','esCombo','pesoTotal']})
                    pair_stats[(depkey,physical)].add(statistical)
                    delta=(end-date.fromisoformat(day)).days
                    for window in (7,30,60):
                        if 0<=delta<window:
                            pairs[(depkey,physical)][f'packs_{window}']+=number(raw.get('cantidadesTotal')) or ZERO
                            pairs[(depkey,physical)][f'closed_{window}']+=number(raw.get('cantidadSolicitada')) or ZERO
                            pairs[(depkey,physical)][f'loose_{window}']+=number(raw.get('unidadesSolicitadas')) or ZERO
                doc=canonical([raw.get(k) for k in ['idEmpresa','idDocumento','letra','serie','nrodoc']])
                doc_deposits[doc].add(depkey)
                if raw.get('idLinea') not in (None,''):line_keys[(day,doc,str(raw['idLinea']))]+=1
                else:totals['missing_line_id']+=1
        compact=_compact_records(records);result=reconcile(baseline[day],compact)
        atomic_json(root/'reconciliation'/(day+'.json'),result)
        all_v1.extend(baseline[day]);all_v2.extend(compact)
        report['days'].append({'day':day,'status':'compared','source_rows':allrows,'included_rows':len(records),
            'excluded':dict(excluded),'source_and_storage_equal':load['raw_roundtrip_verified'],
            'source_document_count_verified':reported_docs=={len(rawdocs)},'source_documents':len(rawdocs),
            'reported_documents':sorted(reported_docs),
            'v1_rows':len(baseline[day]),'v2_equivalent_rows':len(compact),'consistent':result['consistent'],
            'difference_count':result['difference_count'],'entity_sets_equal':result['entity_sets_equal'],
            'v1_metrics':result['baseline']['metrics'],'v2_metrics':result['candidate']['metrics'],
            'capture_seconds':entry['seconds'],'load_seconds':load['seconds'],
            'physical_present':physical_present,'physical_missing':physical_missing,'sheet_rows':sheet_rows})
    global_recon=reconcile(all_v1,all_v2)
    atomic_json(root/'reconciliation'/'global.json',global_recon)
    report['reconciliation']={k:v for k,v in global_recon.items() if k!='differences'}
    report['totals']=dict(totals)
    report['deposit_coverage']={'rows_pct':100*totals['deposit_present']/totals['included_rows'] if totals['included_rows'] else None,
        'raw_rows_pct':100*totals['raw_deposit_present']/totals['raw_rows'] if totals['raw_rows'] else None,
        'net_revenue_pct':float(100*covered_net/total_net) if total_net else None,
        'absolute_net_revenue_pct':float(100*covered_absolute/absolute_net) if absolute_net else None,
        'net_revenue':str(total_net),'direct_raw_net':str(raw_net),'direct_raw_final':str(raw_final)}
    for dep,a in deposits.items():
        report['deposits'][dep]={k:(len(v) if k in ('products','sheets') else sorted(v) if isinstance(v,set) else str(v) if isinstance(v,Decimal) else v) for k,v in a.items()}
    report['sheets']={'distinct':len(sheets),'multiple_deposits':sum(len(v['deposits'])>1 for v in sheets.values()),
        'multiple_companies':sum(len(v['companies'])>1 for v in sheets.values()),'multiple_forces':sum(len(v['forces'])>1 for v in sheets.values()),'multiple_channels':sum(len(v['channels'])>1 for v in sheets.values())}
    phys_stats=defaultdict(set);stats_phys=defaultdict(set)
    for (physical,statistical),count in identities.items():
        if physical and statistical:phys_stats[physical].add(statistical);stats_phys[statistical].add(physical)
    report['identity']={'physical_products':len(products),'physical_row_pct':100*totals['physical_present']/totals['included_rows'] if totals['included_rows'] else None,
        'physical_with_multiple_statistical':sum(len(v)>1 for v in phys_stats.values()),'statistical_with_multiple_physical':sum(len(v)>1 for v in stats_phys.values()),
        'missing_line_id_rows':totals['missing_line_id'],'repeated_document_line_keys':sum(n>1 for n in line_keys.values()),
        'documents_multiple_deposits':sum(len(v)>1 for v in doc_deposits.values())}
    import csv
    with (output/'deposit-company-force-channel.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['deposit_id','company_id','sales_force_id','channel','rows','amount_net','commercial_quantity','physical_products'])
        for key,v in sorted(dimensional.items()):writer.writerow([*key,v['rows'],v['net'],v['quantity'],len(v['products'])])
    auxiliary=root/'auxiliary.json'
    if auxiliary.exists() and json.loads(auxiliary.read_text()).get('status')=='complete':
        aux=json.loads(auxiliary.read_text());master=defaultdict(list);stock=defaultdict(lambda:{'packs':ZERO,'loose':ZERO,'rows':0,'captured_at':None});stock_products=set()
        for p in aux['articles']:
            for r in read_payload(root,p).get('Articulos',{}).get('eArticulos',[]):
                physical=identifier(r.get('idArticulo'))
                if physical:master[physical].append(r)
        stockbad=set()
        for p in aux['stock']:
            for r in read_payload(root,p).get('dsStockFisicoApi',{}).get('dsStock',[]):
                physical=identifier(r.get('idArticulo'));dep=identifier(r.get('idDeposito'))
                if not physical or not dep:continue
                k=(dep,physical);stock_products.add(physical);s=stock[k];s['rows']+=1;s['captured_at']=p['finished_at']
                packs,loose=number(r.get('cantBultos')),number(r.get('cantUnidades'))
                if packs is None or loose is None:stockbad.add(k)
                else:s['packs']+=packs;s['loose']+=loose
        evidence={};reason_counts=Counter();compatible=set()
        def yes(v):return v is True or str(v).strip().upper() in ('SI','TRUE','1')
        def known_flag(v):return isinstance(v,bool) or str(v).strip().upper() in ('SI','NO','TRUE','FALSE','0','1')
        for physical,rows in products.items():
            ms=master.get(physical,[])
            signatures={(str(m.get('unidadesBulto')),str(m.get('pesable')),str(m.get('esCombo'))) for m in ms}
            if len(signatures)!=1:
                e={'observed_compatible':False,'reasons':['MASTER_MISSING' if not ms else 'MASTER_CONFLICT'],'uxb':None}
            else:
                m=ms[0];e=unit_evidence(rows,m.get('unidadesBulto'),pesable=yes(m.get('pesable')),combo=yes(m.get('esCombo')) or any(yes(r.get('esCombo')) for r in rows))
                if not known_flag(m.get('pesable')) or not known_flag(m.get('esCombo')) or any(not known_flag(r.get('esCombo')) for r in rows):
                    e['observed_compatible']=False;e['reasons'].append('WEIGHABLE_OR_COMBO_FLAG_UNKNOWN')
                e['master_id']=physical;e['master_unit']=m.get('desUnidadMedida');e['master_unit_value']=m.get('valorUnidadMedida')
            evidence[physical]=e
            if e['observed_compatible']:compatible.add(physical)
            reason_counts.update(e['reasons'])
        atomic_json(output/'unit-evidence.json',evidence)
        experimental=[];eligibility=Counter();covered_products=set();eligible_pairs=0
        for (dep,physical),sales in pairs.items():
            k=(dep,physical);ev=evidence[physical]
            reasons=[]
            if dep=='UNKNOWN':reasons.append('DEPOSIT_MISSING')
            if k not in stock:reasons.append('STOCK_NOT_OBSERVED')
            if k in stockbad:reasons.append('STOCK_QUANTITY_INVALID')
            if not ev['observed_compatible']:reasons.append('UNIT_UNVERIFIED')
            if len([d for d in report['days'] if d.get('source_and_storage_equal') and d.get('source_document_count_verified')])!=60:reasons.append('HISTORY_INCOMPLETE')
            if reasons:eligibility.update(reasons);continue
            u=Decimal(ev['uxb']);s=stock[k];stock_units=s['packs']*u+s['loose']
            unit_sales={w:sales[f'closed_{w}']*u+sales[f'loose_{w}'] for w in (7,30,60)}
            if stock_units<0:eligibility['NEGATIVE_STOCK']+=1;continue
            if stock_units!=stock_units.to_integral_value():eligibility['NONINTEGER_STOCK_PIECES']+=1;continue
            if unit_sales[60]<=0:eligibility['NONPOSITIVE_60D_NET_SALES']+=1;continue
            eligible_pairs+=1;covered_products.add(physical)
            experimental.append({'deposit_id':dep,'physical_article_id':physical,'article_name':names[physical],
                'uxb':str(u),'stock_bultos':str(s['packs']),'stock_sueltas':str(s['loose']),'stock_units':str(stock_units),
                **{f'sales_units_{w}':str(unit_sales[w]) for w in (7,30,60)},
                **{f'average_daily_units_{w}':str(unit_sales[w]/w) for w in (7,30,60)},
                **{f'theoretical_days_{w}':str(stock_units/(unit_sales[w]/w)) if unit_sales[w]>0 else None for w in (7,30,60)},
                'captured_at':s['captured_at'],'sales_end':str(end),'experimental':True,'policy_classification':None,
                'current_day_sales_excluded':True,'units_evidence':'physical_packs_loose_and_per_line_presentation_vs_current_master'})
        report['unit_audit']={'sales_physical_products':len(products),'master_physical_products':len(master),'stock_physical_products':len(stock_products),
            'compatible_products':len(compatible),'compatible_pct':100*len(compatible)/len(products) if products else None,
            'reasons':dict(reason_counts),'eligible_deposit_product_pairs':eligible_pairs,'sales_deposit_product_pairs':len(pairs),
            'eligible_pair_pct':100*eligible_pairs/len(pairs) if pairs else None,'products_with_experimental_coverage':len(covered_products),
            'products_with_experimental_coverage_pct':100*len(covered_products)/len(products) if products else None,
            'eligibility_exclusions':dict(eligibility),'stock_capture_started':aux['stock'][0]['started_at'],'stock_capture_finished':aux['finished_at'],
            'historical_master_validity_certified':False,'basis':'numerical_identity_for_all_observed_sales_rows_and_current_physical_master',
            'stock_expected_deposits':aux['expected_deposits']}
        report['identity'].update(products_found_in_current_master=len(set(products)&set(master)),
            products_found_in_stock=len(set(products)&stock_products),
            rows_with_physical_id_in_master=sum(count for (physical,_),count in identities.items() if physical in master))
        report['unit_audit']['experimental_operationally_certified']=False
        report['unit_audit']['limitations']=['current_master_not_a_historical_master_archive','consumeStock_not_certified','snapshot_not_a_sales_period_close','net_commercial_sales_not_verified_physical_dispatches']
        experimental.sort(key=lambda x:(-float(x['sales_units_60']),int(x['deposit_id']),int(x['physical_article_id'])))
        atomic_json(output/'experimental-all.json',experimental);report['experimental']=experimental[:20]
    metas=[json.loads(p.read_text()) for p in (root/'raw').rglob('*.meta.json')]
    with (output/'page-timings.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['day','pass','page','attempt','started_at','finished_at','seconds','bytes','http_status','success','error_type','sha256'])
        for path in sorted((root/'raw').rglob('*.meta.json')):
            m=json.loads(path.read_text());writer.writerow([m['params'].get('fechaDesde'),path.parent.parent.name,m['params'].get('nroLote'),m['attempt'],m['started_at'],m['finished_at'],m['seconds'],m.get('bytes'),m.get('http_status'),m['success'],m.get('error_type'),m.get('sha256')])
    successes=[m for m in metas if m.get('success')];seconds=[m['seconds'] for m in successes]
    report['timing']={'sales_http_requests':len(metas),'successful_requests':len(successes),'failed_http_requests':len(metas)-len(successes),
        'retries':sum(m.get('attempt',1)>1 for m in metas),'downloaded_bytes':sum(m.get('bytes',0) for m in successes),
        'http_seconds':sum(m['seconds'] for m in metas),'page_seconds_median':statistics.median(seconds) if seconds else None,
        'page_seconds_mean':statistics.mean(seconds) if seconds else None,'page_seconds_max':max(seconds) if seconds else None,
        'page_seconds_p95':sorted(seconds)[min(len(seconds)-1,int(len(seconds)*.95))] if seconds else None,
        'selected_day_capture_seconds':sum(d.get('capture_seconds',0) for d in report['days']),
        'selected_load_seconds':sum(d.get('load_seconds',0) for d in report['days'])}
    from datetime import datetime
    if metas:
        first=min(m['started_at'] for m in metas);last=max(m['finished_at'] for m in metas)
        report['timing'].update(first_request_at=first,last_response_at=last,wall_seconds=(datetime.fromisoformat(last)-datetime.fromisoformat(first)).total_seconds())
    auxiliary_metas=[json.loads(p.read_text()) for p in (root/'auxiliary').rglob('*.meta.json')] if (root/'auxiliary').exists() else []
    report['timing']['auxiliary']={'requests':len(auxiliary_metas),'seconds':sum(m['seconds'] for m in auxiliary_metas),
        'bytes':sum(m.get('bytes',0) for m in auxiliary_metas),'failed_requests':sum(not m.get('success') for m in auxiliary_metas)}
    with (output/'daily-results.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['day','status','source_rows','included_rows','v1_rows','v2_equivalent_rows','consistent','differences','capture_seconds','load_seconds','v1_net','v2_net','v1_quantity','v2_quantity'])
        for d in report['days']:writer.writerow([d['day'],d['status'],d.get('source_rows'),d.get('included_rows'),d.get('v1_rows'),d.get('v2_equivalent_rows'),d.get('consistent'),d.get('difference_count'),d.get('capture_seconds'),d.get('load_seconds'),d.get('v1_metrics',{}).get('amount_net'),d.get('v2_metrics',{}).get('amount_net'),d.get('v1_metrics',{}).get('quantity'),d.get('v2_metrics',{}).get('quantity')])
    from sales_coach.repositories.sales_v2_local_repository import LocalSalesV2Repository
    repo=LocalSalesV2Repository(root)
    report['storage']=json.loads(repo.query("SELECT sum(rows) stored_rows,sum(bytes_on_disk) bytes_on_disk,sum(data_uncompressed_bytes) uncompressed FROM system.parts WHERE active AND database='default' FORMAT JSONEachRow"))
    report['storage']['by_table']=[json.loads(s) for s in repo.query("SELECT table,sum(rows) rows,sum(bytes_on_disk) bytes,sum(data_uncompressed_bytes) uncompressed FROM system.parts WHERE active AND database='default' GROUP BY table FORMAT JSONEachRow").splitlines()]
    report['storage']['raw_gzip_bytes']=sum(p.stat().st_size for p in (root/'raw').rglob('*.gz'))
    report['storage']['private_total_file_bytes']=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    report['analysis_seconds']=time.monotonic()-tick
    report['completed_days']=sum(d.get('status')=='compared' for d in report['days'])
    report['commercial_consistent_days']=sum(d.get('consistent') is True for d in report['days'])
    atomic_json(output/'audit.json',report)
    print(canonical({'days':report['completed_days'],'consistent':report['commercial_consistent_days'],'included':totals['included_rows'],'units':report['unit_audit']}),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();analyze(args.root,args.output)
