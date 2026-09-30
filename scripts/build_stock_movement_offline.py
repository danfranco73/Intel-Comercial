"""Build an isolated V2 economic/physical revision and director brief from archives only."""
import socket

def offline(*a,**k):raise RuntimeError('OFFLINE: network disabled')
socket.socket.connect=offline
socket.socket.connect_ex=offline
socket.create_connection=offline
import argparse,csv,gzip,hashlib,json,sys
from pathlib import Path
from decimal import Decimal as D
from collections import defaultdict,Counter
from datetime import date,datetime,timezone
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sales_coach.domain.sales_v2 import canonical,identifier,number,multiset_hash
from sales_coach.domain.sales_v2_movement import model,VERSION,window_result,stock_observations
from sales_coach.repositories.sales_v2_local_repository import LocalSalesV2Repository
Z=D(0)

def save(path,x):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
def csvwrite(path,rows):
    if not rows:path.write_text('');return
    with path.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def payload(root,p):
    b=gzip.open(root/p['path'],'rb').read();assert hashlib.sha256(b).hexdigest()==p['sha256'];return json.loads(b)
def no(v):return v is False or str(v).upper() in ('NO','FALSE','0')
def yes(v):return v is True or str(v).upper() in ('SI','TRUE','1')

def run(root,out):
    out.mkdir(parents=True,exist_ok=True);base=json.loads((root/'candidate.json').read_text())
    aux=json.loads((root/'auxiliary.json').read_text());assert aux['status']=='complete'
    old=json.loads(Path('docs/intelligence/sales-v2-pilot/audit.json').read_text())
    evidence=json.loads(Path('docs/intelligence/sales-v2-pilot/unit-evidence.json').read_text())
    reconciled={r['day']:r for r in csv.DictReader(Path('docs/intelligence/sales-v2-offline-diagnosis/daily-reconciliation.csv').open())}
    end=date.fromisoformat(old['window']['end']);assert old['completed_days']==60
    baseline_sha=hashlib.sha256((root/'baseline.jsonl.gz').read_bytes()).hexdigest();assert baseline_sha==base['baseline_sha256']
    release=uuid4().hex;work=root/'movement-revisions'/release;work.mkdir(parents=True);stage=work/'lines.jsonl'
    save(work/'attempt.json',{'revision_id':release,'status':'MODELING','official':False})
    master=defaultdict(list)
    for p in aux['articles']:
        for r in payload(root,p)['Articulos']['eArticulos']:
            if identifier(r.get('idArticulo')):master[identifier(r['idArticulo'])].append(r)
    units={};master_names={};blocked=defaultdict(set)
    for product,rs in master.items():
        sig={(str(r.get('unidadesBulto')),str(r.get('pesable')),str(r.get('esCombo'))) for r in rs};m=rs[0];u=number(m.get('unidadesBulto'))
        master_names[product]=m.get('desArticulo',product)
        if len(sig)==1 and u and u>0 and u==u.to_integral_value() and no(m.get('pesable')) and no(m.get('esCombo')):
            if product not in evidence or evidence[product]['observed_compatible']:units[product]=u
    stocks=defaultdict(lambda:{'bultos':Z,'sueltas':Z,'rows':0,'warehouses':set(),'lots':[],'captured_at':None})
    stockinvalid=set()
    for pg in aux['stock']:
        for r in payload(root,pg)['dsStockFisicoApi']['dsStock']:
            key=(identifier(r.get('idDeposito')),identifier(r.get('idArticulo')))
            if not all(key):continue
            s=stocks[key];b,l=number(r.get('cantBultos')),number(r.get('cantUnidades'))
            if b is None or l is None:stockinvalid.add(key);continue
            s['bultos']+=b;s['sueltas']+=l;s['rows']+=1;s['warehouses'].add(str(r.get('idAlmacen')));s['captured_at']=pg['finished_at']
            master_names.setdefault(key[1],r.get('dsArticulo',key[1]))
            if r.get('fecVtoLote'):s['lots'].append({'warehouse':r.get('idAlmacen'),'expiry':r['fecVtoLote'],'bultos':b,'sueltas':l})
    metrics=defaultdict(lambda:defaultdict(lambda:Z));counts=Counter();daily=[];names={};unknown=[];bonus=Counter();total_net=Z;all_final=Z
    with stage.open('w') as f:
        for cp in sorted((root/'days').glob('*.json')):
            day=cp.stem;e=json.loads(cp.read_text());selected=json.loads((root/'selected'/cp.name).read_text());assert selected['capture_id']==e['capture_id'] and e['status']=='source_verified'
            raw=[]
            for pg in e['passes'][0]['pages']:raw.extend(payload(root,pg)['dsReporteComprobantesApi'].get('VentasResumen',[]))
            assert multiset_hash(raw)==selected['source_digest']
            net=Z;final=Z;dayrows=0
            for ordinal,r in enumerate(raw):
                product=identifier(r.get('idArticulo'));dep=identifier(r.get('idDeposito'));key=(dep,product)
                m=model(r,uxb=units.get(product),unit_approved=product in units)
                u=units.get(product);ph=m['physical'];ec=m['economic'];cancelled=m['cancelled'];counts['raw_rows']+=1
                if not cancelled:
                    assert ec['net'] is not None and ec['final'] is not None
                    net+=ec['net'];final+=ec['final'];dayrows+=1
                    if product:names[product]=r.get('dsArticulo',product)
                    if 'FULL_DISCOUNT_ECONOMIC_CONFLICT' in m['issues']:raise ValueError('Unexpected discount conflict')
                    if product and not ph['included']:blocked[product].update(m['issues'])
                    if not product and number(r.get('cantidadesTotal'))!=0:unknown.append({'day':day,'quantity':r.get('cantidadesTotal'),'type':r.get('idDocumento')})
                    if dep and product:
                        v=metrics[key];v['rows']+=1
                        if ph['included']:
                            parts=ph['pieces']
                            for w in (7,30,60):
                                if (end-date.fromisoformat(day)).days<w:
                                    for k in ('outbound','returns','other_negative'):v[f'{k}_{w}']+=ph[k]
                                    v[f'charged_net_{w}']+=parts['charged'];v[f'free_net_{w}']+=parts['free'];v[f'physical_net_{w}']+=parts['total']
                                    v[f'activity_{w}']+=abs(parts['total'])
                                    v[f'free_outbound_{w}']+=max(parts['free'],Z)
                            if ec['net']==0 and parts['total']!=0:counts['zero_net_nonzero_physical_lines']+=1
                            if ec['discount_percent']==100 and ec['gross']!=0:
                                bonus['convertible_bonus_lines']+=1;bonus['signed_free_units']+=parts['free'];bonus['outbound_free_units']+=max(parts['free'],Z);bonus['negative_free_units']+=-min(parts['free'],Z)
                # Independent economic and physical inclusion; zeros never suppress a row.
                item={'revision_id':release,'version':VERSION,'base_load_id':selected['load_id'],'date':day,'ordinal':ordinal,
                    'raw_hash':hashlib.sha256(canonical(r).encode()).hexdigest(),'deposit_id':dep,'physical_article_id':product,
                    'document_type':m['document_type'],'cancelled':int(cancelled),
                    'gross':str(ec['gross']) if ec['gross'] is not None else None,'discount':str(ec['discount']) if ec['discount'] is not None else None,
                    'net':str(ec['net']) if ec['net'] is not None else None,'final':str(ec['final']) if ec['final'] is not None else None,
                    'charged_original':str(ph['charged_original']) if ph['charged_original'] is not None else None,
                    'free_original':str(ph['free_original']) if ph['free_original'] is not None else None,
                    'total_original':str(ph['total_original']) if ph['total_original'] is not None else None,
                    'uxb':str(u) if u is not None else None,'physical_units':str(ph['pieces']['total']) if ph['pieces'] else None,
                    'physical_valid':int(ph['included']),'model_json':canonical(m)}
                f.write(canonical(item)+'\n')
            prev=reconciled[day];assert net==D(prev['v2_net']) and net-D(prev['v1_net'])==D(prev['explained_delta'])
            daily.append({'day':day,'raw_rows':len(raw),'included_rows':dayrows,'v1_net':prev['v1_net'],'previous_v2_net':prev['v2_net'],'revised_v2_net':net,'v1_delta_explained':net-D(prev['v1_net']),'unexplained_delta':0})
            total_net+=net;all_final+=final;print(day,'offline model reconciled',flush=True)
    assert len(daily)==60 and total_net==D(old['deposit_coverage']['net_revenue'])
    save(work/'attempt.json',{'revision_id':release,'status':'MODELED','rows':counts['raw_rows'],'official':False})
    # Isolated append-only revision, never ALTER/DELETE V1 or the original pilot.
    repo=LocalSalesV2Repository(root)
    repo.query('CREATE TABLE IF NOT EXISTS sales_economic_physical_v2 (revision_id String,version String,base_load_id String,date Date,ordinal UInt32,raw_hash FixedString(64),deposit_id Nullable(String),physical_article_id Nullable(String),document_type String,cancelled UInt8,gross Nullable(Decimal(38,12)),discount Nullable(Decimal(38,12)),net Nullable(Decimal(38,12)),final Nullable(Decimal(38,12)),charged_original Nullable(Decimal(38,12)),free_original Nullable(Decimal(38,12)),total_original Nullable(Decimal(38,12)),uxb Nullable(Decimal(38,12)),physical_units Nullable(Decimal(38,12)),physical_valid UInt8,model_json String CODEC(ZSTD)) ENGINE=MergeTree ORDER BY (revision_id,date,ordinal)')
    path=str(stage).replace("'","\\'");repo.query(f"INSERT INTO sales_economic_physical_v2 FROM INFILE '{path}' FORMAT JSONEachRow")
    check=json.loads(repo.query(f"SELECT count() rows,uniqExact(tuple(date,ordinal)) unique_rows,toString(sumIf(net,cancelled=0)) net,toString(sumIf(`final`,cancelled=0)) final_amount FROM sales_economic_physical_v2 WHERE revision_id='{release}' FORMAT JSONEachRow"))
    assert int(check['rows'])==int(check['unique_rows'])==counts['raw_rows'] and D(check['net'])==total_net and D(check['final_amount'])==all_final
    inventory=[];excluded=Counter();allpairs=set(stocks)|set(metrics)
    for key in sorted(allpairs,key=lambda k:(int(k[0]),int(k[1]))):
        dep,product=key;s=stocks.get(key);v=metrics[key];u=units.get(product);reasons=[]
        if not s:reasons.append('STOCK_NOT_OBSERVED')
        if not u:reasons.append('UNIT_UNVERIFIED')
        if product in blocked:reasons.extend(sorted(blocked[product]))
        if key in stockinvalid:reasons.append('STOCK_INVALID')
        stock=s['bultos']*u+s['sueltas'] if s and u else None
        if stock is not None and stock!=stock.to_integral_value():reasons.append('NONINTEGER_STOCK')
        eligible=not reasons
        if not eligible:excluded.update(set(reasons))
        item={'deposit_id':dep,'physical_article_id':product,'product':names.get(product,master_names.get(product,product)),
            'eligible':eligible,'exclusions':sorted(set(reasons)),'stock_bultos':str(s['bultos']) if s else None,
            'stock_sueltas':str(s['sueltas']) if s else None,'stock_units':str(stock) if stock is not None else None,
            'uxb':str(u) if u else None,'stock_captured_at':s['captured_at'] if s else None,'sales_end':str(end),
            'unit_basis':'master_and_observed_sales' if product in evidence else 'current_master_no_sales_observed',
            'warehouses':sorted(s['warehouses']) if s else [],'observations':[],'expiry_lots':[],
            'deposit_type':'transient' if dep=='5' else 'not_inferred','expected_balance':0 if dep=='5' else None}
        if eligible:
            for w in (7,30,60):
                result=window_result(v[f'outbound_{w}'],v[f'returns_{w}'],v[f'other_negative_{w}'],stock,w)
                assert result['net']==v[f'physical_net_{w}']==v[f'charged_net_{w}']+v[f'free_net_{w}']
                item.update({f'{k}_{w}':str(val) if val is not None else None for k,val in result.items()})
                item[f'charged_net_{w}']=str(v[f'charged_net_{w}']);item[f'free_net_{w}']=str(v[f'free_net_{w}']);item[f'free_outbound_{w}']=str(v[f'free_outbound_{w}'])
            item['observations'].extend(stock_observations(stock,v['activity_30'],v['activity_60'],dep))
        # Expiries are preserved even for products excluded from physical conversion.
        if s:
            for lot in s['lots']:
                lu=lot['bultos']*u+lot['sueltas'] if u else None
                item['expiry_lots'].append({**lot,'units':str(lu) if lu is not None else None})
            if item['expiry_lots']:item['observations'].append('EXPIRY_REPORTED')
        inventory.append(item)
    eligible=[x for x in inventory if x['eligible']]
    observations={key:[x for x in inventory if key in x['observations']] for key in ('ZERO_STOCK_WITH_HISTORY','NEGATIVE_STOCK','POSITIVE_NO_MOVEMENT_30','POSITIVE_NO_MOVEMENT_60','EXPIRY_REPORTED','TRANSIENT_NONZERO_BALANCE')}
    # Native quantities remain visible for excluded units as well.
    negative_native=[x for x in inventory if not x['eligible'] and (D(x['stock_bultos'] or 0)<0 or D(x['stock_sueltas'] or 0)<0)]
    save(out/'inventory.json',inventory);save(out/'observations.json',observations);save(out/'excluded-negative-stock.json',negative_native)
    flat=[{k:v for k,v in x.items() if k not in ('expiry_lots','warehouses','exclusions','observations')} for x in eligible]
    csvwrite(out/'eligible-deposit-products.csv',flat);csvwrite(out/'daily-reconciliation.csv',daily)
    stocks_summary={}
    for dep in sorted({x['deposit_id'] for x in inventory},key=int):
        ds=[x for x in inventory if x['deposit_id']==dep];good=[x for x in ds if x['eligible']]
        stocks_summary[dep]={'observed_in_stock':dep in aux['expected_deposits'],'pairs':len(ds),'eligible_pairs':len(good),
            **{k:sum(k in x['observations'] for x in good) for k in ('ZERO_STOCK_WITH_HISTORY','NEGATIVE_STOCK','POSITIVE_NO_MOVEMENT_30','POSITIVE_NO_MOVEMENT_60')},
            'expiry_pairs':sum(bool(x['expiry_lots']) for x in ds)}
    report={'version':VERSION,'revision_id':release,'base_release_id':base['release_id'],'official':False,'offline':True,
        'economic_rule':'explicit_net_zero_preserved_free_goods_retain_physical_units','physical_basis':'sales_lines_not_a_certified_dispatch_ledger',
        'window':{'start':old['window']['start'],'end':str(end),'days':60},'stock_capture_start':aux['stock'][0]['started_at'],'stock_capture_end':aux['finished_at'],
        'baseline_sha256':baseline_sha,'counts':dict(counts),'bonus':dict(bonus),'native_verification':check,
        'net':str(total_net),'all_pairs':len(inventory),'eligible_pairs':len(eligible),'eligible_products':len({x['physical_article_id'] for x in eligible}),
        'eligible_pairs_positive_60d_net':sum(D(x['net_60'])>0 and D(x['stock_units'])>=0 for x in eligible),
        'exclusions':dict(excluded),'unknown_article_nonzero_quantity_rows':len(unknown),
        'observations':{k:len(v) for k,v in observations.items()},'excluded_native_negative_pairs':len(negative_native),'deposits':stocks_summary,
        'deposit5':{'type':'TRANSIENT','expected_balance':0,'observed_in_archived_stock':'5' in aux['expected_deposits'],'balance':None,'status':'NOT_OBSERVED'},
        'limitations':['Stock is an archived observation, not live.','No deposit 5 stock was downloaded.','consumeStock was not certified; physical figures derive from commercial lines.','Current article master is not a historical master archive.','Absence of movement requires zero gross physical activity, not merely zero net movement.']}
    repo.query(f"CREATE OR REPLACE VIEW sales_economic_physical_selected_v2 AS SELECT * FROM sales_economic_physical_v2 WHERE revision_id='{release}'")
    report['local_selected_view']='sales_economic_physical_selected_v2'
    save(out/'summary.json',report);save(work/'complete.json',report)
    save(work/'attempt.json',{'revision_id':release,'status':'VERIFIED_EXPERIMENTAL','official':False})
    save(out/'provenance.json',{'source_baseline_sha256':baseline_sha,'source_candidate_sha256':hashlib.sha256((root/'candidate.json').read_bytes()).hexdigest(),'auxiliary_manifest_sha256':hashlib.sha256((root/'auxiliary.json').read_bytes()).hexdigest(),'derived_lines_sha256':hashlib.sha256(stage.read_bytes()).hexdigest(),'network_used':False,'production_changes':False})
    print(canonical(report),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();run(a.root.expanduser().resolve(),a.output)
