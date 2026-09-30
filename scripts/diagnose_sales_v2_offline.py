"""Offline forensic reconciliation. Reads frozen archives; never queries services.

Only writes a new diagnostic report and private evidence. Does not correct V1/V2.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import csv
import gzip
import hashlib
import json
from pathlib import Path
import socket
import sys


def no_network(*args, **kwargs):
    raise RuntimeError('Network disabled for the offline diagnosis')


# Guard even against accidentally invoking a network helper in imported modules.
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from erp_client import normalize_erp_sale_row
from clickhouse_client import _compact_records
from sales_coach.domain.sales_v2 import canonical, digest, equivalent_key, multiset_hash

Z = Decimal(0)


def dec(value):
    return Decimal(str(value)) if value not in (None, '') else Z


def total(rows, field):
    return sum((dec(r.get(field)) for r in rows), Z)


def token(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:16]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def csvfile(path, rows):
    if not rows:
        path.write_text(''); return
    with path.open('w') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def run(root, output):
    output.mkdir(parents=True, exist_ok=True)
    private = root/'offline-diagnosis'; private.mkdir(exist_ok=True)
    baseline_meta = json.loads((root/'baseline-meta.json').read_text())
    assert hashlib.sha256((root/'baseline.jsonl.gz').read_bytes()).hexdigest() == baseline_meta['sha256']
    baseline = defaultdict(list)
    for s in gzip.open(root/'baseline.jsonl.gz', 'rt'):
        r = json.loads(s); baseline[r['date']].append(r)
    candidates = json.loads((root/'candidate.json').read_text())
    releases = [json.loads(s) for s in (root/'releases'/candidates['release_id']/'days.jsonl').read_text().splitlines()]
    assert digest(releases) == candidates['manifest_sha256']
    release_by_day = {r['date']:r for r in releases}
    categories = {c:{'cases':0,'v1_rows':0,'v2_rows':0,'delta':Z} for c in 'ABCDEFG'}
    counts = Counter(); audits = Counter(); money = defaultdict(lambda:Z)
    cases = []; affected = []; daily = []; type_data = {}; moves = Counter(); rounding = []
    breakdown = {f:defaultdict(lambda:{'lines':0,'delta':Z}) for f in (
        'day','company','document_type','document','client','physical_product','statistical_product',
        'seller','force','channel','deposit','quantity_sign')}
    matched_old = Z; matched_new = Z; a_only = Z; b_only = Z
    v1_docs=set();v2_docs=set();v1_products=set();v2_products=set()
    def register(category, day, keys_a, keys_b, a, b, raws, reason):
        av = total([a[k] for k in keys_a], 'amount_net'); bv = total([b[k] for k in keys_b], 'amount_net')
        cat = categories[category]; cat['cases'] += 1; cat['v1_rows'] += len(keys_a); cat['v2_rows'] += len(keys_b); cat['delta'] += bv-av
        rr = [r for k in keys_b for r in raws[k]]
        first = rr[0] if rr else {}; key = (keys_b or keys_a)[0]
        item = {'case_id':token([day,keys_a,keys_b]), 'category':category,'reason':reason,'day':day,
            'company':str(first.get('idEmpresa', a.get(key,{}).get('company_key',''))),
            'document_type':first.get('idDocumento', str(a.get(key,{}).get('invoice','')).split('-')[0]),
            'document':token([first.get('idEmpresa'),key[2]]),'client':token(key[1]),
            'physical_products':sorted({str(r.get('idArticulo')) for r in rr}),
            'statistical_products':sorted({str(r.get('idArticuloEstadistico')) for r in rr}),
            'seller':token(key[4]),'force':key[5],'channel':key[7],
            'deposits':sorted({str(r.get('idDeposito')) for r in rr}),
            'v1_net':av,'v2_net':bv,'delta':bv-av,'v1_rows':len(keys_a),'v2_rows':len(keys_b),
            'raw_lines':len(rr),'v1_quantity':total([a[k] for k in keys_a],'quantity'),
            'v2_quantity':total([b[k] for k in keys_b],'quantity')}
        cases.append(item)
        return item
    for cp in sorted((root/'days').glob('*.json')):
        day=cp.stem; entry=json.loads(cp.read_text()); selected=json.loads((root/'selected'/cp.name).read_text())
        assert entry['status']=='source_verified' and release_by_day[day]['load_id']==selected['load_id']
        assert selected['capture_id']==entry['capture_id'] and selected['raw_roundtrip_verified']
        raw=[]
        for page in entry['passes'][0]['pages']:
            body=gzip.open(root/page['path'],'rb').read()
            assert hashlib.sha256(body).hexdigest()==page['sha256']
            raw.extend(json.loads(body)['dsReporteComprobantesApi'].get('VentasResumen',[]))
        assert multiset_hash(raw)==entry['passes'][0]['digest']==entry['passes'][1]['digest']==selected['source_digest']
        # Read the already persisted local staging representation used for INSERT.
        staged=root/'staging'/selected['load_id']/'lines.jsonl'
        staged_raw=[]; stored_net=Z; stored_final=Z
        for s in staged.open():
            row=json.loads(s); staged_raw.append(json.loads(row['raw_json']))
            stored_net+=dec(row['amount_net']); stored_final+=dec(row['amount_final'])
        assert multiset_hash(staged_raw)==selected['source_digest']
        counts['verified_source_and_persisted_rows']+=len(raw)
        rows=[]; raws=defaultdict(list); linekeys=Counter(); rawhash=Counter(); excluded=[]
        for r in raw:
            linekeys[canonical([r.get(k) for k in ('idEmpresa','idDocumento','letra','serie','nrodoc','idLinea')])]+=1
            rawhash[digest(r)]+=1
            n=normalize_erp_sale_row(r); typ=str(r.get('idDocumento'))
            t=type_data.setdefault(typ,{'description':r.get('dsDocumento'),'raw_rows':0,'included_rows':0,'excluded_rows':0,
                'raw_included_net':Z,'raw_excluded_net':Z,'v1_net':Z,'v2_net':Z,'delta':Z,'bonus_delta':Z,'bonus_lines':0,
                'negative_quantity_rows':0,'negative_net_rows':0})
            t['raw_rows']+=1
            if not n:
                t['excluded_rows']+=1;t['raw_excluded_net']+=dec(r.get('subtotalNeto'));excluded.append(r);continue
            t['included_rows']+=1;t['raw_included_net']+=dec(r.get('subtotalNeto'))
            t['negative_quantity_rows']+=dec(r.get('cantidadesTotal'))<0;t['negative_net_rows']+=dec(r.get('subtotalNeto'))<0
            rows.append(n);raws[equivalent_key(n)].append(r)
            audits['missing_source_net']+=r.get('subtotalNeto') is None
            audits['negative_quantity_rows']+=dec(r.get('cantidadesTotal'))<0
            audits['negative_net_rows']+=dec(r.get('subtotalNeto'))<0
            audits['physical_statistical_different_positive']+=bool(r.get('idArticuloEstadistico') and r.get('idArticuloEstadistico')!=r.get('idArticulo'))
        audits['duplicate_source_document_line_keys']+=sum(v-1 for v in linekeys.values() if v>1)
        audits['duplicate_original_rows']+=sum(v-1 for v in rawhash.values() if v>1)
        compact=_compact_records(rows);aa=baseline[day]
        for records,docs,products in [(aa,v1_docs,v1_products),(compact,v2_docs,v2_products)]:
            for row in records:
                docs.add(token([int(row['company_key']),row['invoice']]))
                products.add(row['product_key'])
        a={equivalent_key(r):r for r in aa};b={equivalent_key(r):r for r in compact}
        audits['duplicate_v1_compact_keys']+=len(aa)-len(a)
        assert len(a)==len(aa) and len(b)==len(compact)
        assert total(rows,'amount_net')==stored_net==total(compact,'amount_net')
        assert total(rows,'amount_final')==stored_final==total(compact,'amount_final')
        assert stored_net==sum((dec(r.get('subtotalNeto')) for r in raw if normalize_erp_sale_row(r)),Z)
        start=len(cases); bonusday=Z
        counts['v1_rows']+=len(aa);counts['v2_rows']+=len(compact);counts['v2_included_raw_rows']+=len(rows)
        counts['excluded_raw_rows']+=len(excluded)
        money['v1_net']+=total(aa,'amount_net');money['v2_net']+=stored_net
        money['v1_final']+=total(aa,'amount_final');money['v2_final']+=stored_final
        money['v1_quantity']+=total(aa,'quantity');money['v2_quantity']+=total(compact,'quantity')
        money['excluded_raw_net']+=total(excluded,'subtotalNeto')
        for r in aa:type_data.setdefault(r['invoice'].split('-')[0],{})['v1_net']+=dec(r['amount_net'])
        for r in compact:type_data[r['invoice'].split('-')[0]]['v2_net']+=dec(r['amount_net'])
        for k in sorted(a.keys()&b.keys()):
            av,bv=dec(a[k]['amount_net']),dec(b[k]['amount_net']);matched_old+=av;matched_new+=bv
            delta=bv-av;rr=raws[k]
            assert len({r.get('idEmpresa') for r in rr})==1
            qdelta=dec(b[k]['quantity'])-dec(a[k]['quantity'])
            if qdelta:rounding.append({'day':day,'case_id':token(k),'delta_quantity':qdelta})
            if not delta:continue
            bonus=[r for r in rr if dec(r.get('subtotalNeto'))==0 and dec(r.get('subtotalBruto'))!=0]
            expected=-total(bonus,'subtotalBruto')
            category='C' if delta==expected else 'G'
            item=register(category,day,[k],[k],a,b,raws,'100_percent_bonus_gross_in_V1' if category=='C' else 'unexplained')
            item['bonus_lines']=len(bonus);item['gross_of_zero_net_lines']=total(bonus,'subtotalBruto')
            assert delta==expected
            assert dec(b[k]['amount_final'])-dec(a[k]['amount_final'])==expected
            for r in bonus:
                assert dec(r.get('bonificacion'))==100 and dec(r.get('subtotalFinal'))==0 and r.get('anulado')=='NO'
                value=-dec(r['subtotalBruto']);bonusday+=value
                record={'case_id':item['case_id'],'day':day,'company':str(r.get('idEmpresa')),'document_type':r.get('idDocumento'),
                    'document':token([r.get('idEmpresa'),k[2]]),'client':token(str(r.get('idCliente'))),
                    'physical_product':str(r.get('idArticulo')),'statistical_product':str(r.get('idArticuloEstadistico')),
                    'seller':token(str(r.get('idVendedor'))),'force':str(r.get('idFuerzaVentas')),'channel':k[7],
                    'deposit':str(r.get('idDeposito')),'quantity_sign':'negative' if dec(r.get('cantidadesTotal'))<0 else 'positive' if dec(r.get('cantidadesTotal'))>0 else 'zero',
                    'quantity':dec(r.get('cantidadesTotal')),'gross':dec(r.get('subtotalBruto')),'net':dec(r.get('subtotalNeto')),
                    'final':dec(r.get('subtotalFinal')),'bonus_percent':dec(r.get('bonificacion')),'delta':value,
                    'subtotalBonificado':r.get('subtotalBonificado'),'tipoOperacionDet':r.get('tipoOperacionDet'),
                    'cantidadesCorCargo':r.get('cantidadesCorCargo'),'cantidadesSinCargo':r.get('cantidadesSinCargo')}
                affected.append(record)
                for dimension in breakdown:
                    v=breakdown[dimension][record[dimension]];v['lines']+=1;v['delta']+=value
                type_data[record['document_type']]['bonus_delta']+=value;type_data[record['document_type']]['bonus_lines']+=1
        unmatched_a=set(a)-set(b);unmatched_b=set(b)-set(a)
        a_only+=total([a[k] for k in unmatched_a],'amount_net');b_only+=total([b[k] for k in unmatched_b],'amount_net')
        for omit,dim in [(6,'route_description'),(7,'channel')]:
            ix_a=defaultdict(list);ix_b=defaultdict(list)
            for k in unmatched_a:ix_a[k[:omit]+k[omit+1:]].append(k)
            for k in unmatched_b:ix_b[k[:omit]+k[omit+1:]].append(k)
            for key in sorted(ix_a.keys()&ix_b.keys()):
                ka,kb=ix_a[key],ix_b[key]
                if len(ka)!=1 or len(kb)!=1:continue
                if dec(a[ka[0]]['amount_net'])!=dec(b[kb[0]]['amount_net']):continue
                assert a[ka[0]]['company_key']==b[kb[0]]['company_key']
                item=register('D',day,ka,kb,a,b,raws,'same_operation_changed_'+dim)
                item['changed_dimension']=dim;item['v1_dimension']=a[ka[0]][dim];item['v2_dimension']=b[kb[0]][dim]
                moves[dim]+=1;unmatched_a.difference_update(ka);unmatched_b.difference_update(kb)
        for k in sorted(unmatched_a):register('A',day,[k],[],a,b,raws,'no_counterpart')
        for k in sorted(unmatched_b):
            rr=raws[k];zero=all(dec(b[k].get(f))==0 for f in ('amount_net','amount_final','quantity'))
            category='F' if zero else 'B'
            item=register(category,day,[],[k],a,b,raws,'zero_aggregate_absent_in_V1_possible_suppression' if zero else 'no_counterpart')
            item['positive_quantity_lines']=sum(dec(r.get('cantidadesTotal'))>0 for r in rr)
            item['negative_quantity_lines']=sum(dec(r.get('cantidadesTotal'))<0 for r in rr)
            item['raw_quantity_sum']=total(rr,'cantidadesTotal')
            item['raw_all_money_zero']=all(dec(r.get(f))==0 for r in rr for f in ('subtotalNeto','subtotalFinal','subtotalBruto'))
        diff=stored_net-total(aa,'amount_net');daycases=cases[start:]
        assert sum((x['delta'] for x in daycases),Z)==diff==bonusday
        docs=lambda rs:len({(r.get('company_key'),r.get('invoice')) for r in rs})
        daily.append({'day':day,'v1_net':total(aa,'amount_net'),'v2_net':stored_net,'delta':diff,
            'v1_rows':len(aa),'v2_compact_rows':len(compact),'v2_raw_included_rows':len(rows),'v1_documents':docs(aa),'v2_documents':docs(compact),
            'C_bonus_groups':sum(x['category']=='C' for x in daycases),'D_dimension_groups':sum(x['category']=='D' for x in daycases),
            'F_zero_groups':sum(x['category']=='F' for x in daycases),'explained_delta':bonusday,'unexplained_delta':diff-bonusday})
        print(day,'verified offline',flush=True)
    assert len(daily)==60
    for t in type_data.values():t['delta']=t['v2_net']-t['v1_net'];assert t['delta']==t['bonus_delta']
    delta=money['v2_net']-money['v1_net'];explained=total(affected,'delta')
    pilot_audit=json.loads(Path('docs/intelligence/sales-v2-pilot/audit.json').read_text())
    original_reconciliation=pilot_audit['reconciliation']
    expected_delta=dec(original_reconciliation['candidate']['metrics']['amount_net'])-dec(original_reconciliation['baseline']['metrics']['amount_net'])
    assert delta==explained==expected_delta
    assert all(sum((v['delta'] for v in dim.values()),Z)==delta for dim in breakdown.values())
    assert sum((v['delta'] for v in categories.values()),Z)==delta
    counts['affected_bonus_raw_lines']=len(affected)
    report={'offline':True,'network_connections_allowed':False,'source_release':candidates['release_id'],
        'baseline_sha256':baseline_meta['sha256'],'money':money,'exact_delta':delta,'explained':explained,'unexplained':delta-explained,
        'explained_pct':100,'counts':counts,'categories':categories,'audits':audits,'dimension_changes':moves,
        'strict_key_bridge':{'matched_v1':matched_old,'matched_v2':matched_new,'v1_only':a_only,'v2_only':b_only},
        'types':type_data,'quantity_rounding':{'groups':len(rounding),'net_delta':total(rounding,'delta_quantity'),'max_abs_delta':max((abs(r['delta_quantity']) for r in rounding),default=Z)},
        'entity_impact':{'v1_documents':len(v1_docs),'v2_documents':len(v2_docs),
            'documents_only_v1':len(v1_docs-v2_docs),'documents_only_v2':len(v2_docs-v1_docs),
            'extra_documents_all_in_zero_groups':(v2_docs-v1_docs)<={x['document'] for x in cases if x['category']=='F'},
            'v1_products':len(v1_products),'v2_products':len(v2_products),
            'products_only_v1':sorted(v1_products-v2_products),'products_only_v2':sorted(v2_products-v1_products)},
        'net_change_pct_vs_v1':100*delta/money['v1_net'],'final_change_pct_vs_v1':100*delta/money['v1_final'],
        'top20':sorted([x for x in cases if x['delta']],key=lambda x:(-abs(x['delta']),x['case_id']))[:20]}
    save(output/'diagnosis.json',report);save(output/'cases-sanitized.json',cases)
    csvfile(output/'daily-reconciliation.csv',daily);csvfile(output/'bonus-lines-sanitized.csv',affected)
    csvfile(output/'document-types.csv',[{'type':k,**v} for k,v in sorted(type_data.items())])
    for dim,values in breakdown.items():csvfile(output/f'by-{dim}.csv',[{dim:k,**v} for k,v in sorted(values.items(),key=lambda kv:-abs(kv[1]['delta']))])
    save(private/'quantity-rounding.json',rounding)
    # Detailed original keys remain private. Existing archives and tables are never written.
    save(output/'checks.json',{'baseline_checksum':True,'all_60_source_multisets':True,'persisted_insert_files_match_sources':True,
        'selected_manifest_valid':True,'each_day_balances_exactly':True,'each_dimension_balances_exactly':True,
        'each_document_type_balances_exactly':True,'all_nonzero_deltas_equal_zero_net_bonus_gross':True,
        'source_net_equals_stored_V2_equals_replayed_compact_net':True,
        'prior_native_roundtrip_evidence':str(root/'selected'),'no_network':True})
    print(json.dumps({'exact_delta':str(delta),'explained':str(explained),'categories':categories,'types':type_data},default=str))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();run(a.root.expanduser().resolve(),a.output)
