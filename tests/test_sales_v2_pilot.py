from decimal import Decimal
import pytest
from sales_coach.domain.sales_v2 import normalize, multiset_hash, reconcile, unit_evidence, identifier
from sales_coach.services.sales_v2_capture import Capture, atomic_json
from datetime import date
from pathlib import Path
import gzip
import json
import hashlib


def sale(**changes):
    return {"fechaComprobate":"2026-09-28", "idCliente":1, "subtotalNeto":10,
            "subtotalFinal":12, "idEmpresa":1,"idDocumento":"FCVTA","letra":"A","serie":1,"nrodoc":5,
            "idArticulo":12,"idArticuloEstadistico":99,"idDeposito":5,"dsDeposito":"DEP PRODUNOA",
            "planillaCarga":"0000 - 00000123", "cantidadesTotal":2,"unimedtotal":9,
            "cantidadSolicitada":2,"unidadesSolicitadas":0,"presentacionArticulo":6, **changes}


def test_original_deposit_and_physical_ids_and_no_route_fallback():
    r=normalize(sale(), capture_id="x",page=1,ordinal=0,captured_at="now")
    assert r["idDeposito"]=="5" and r["idArticulo"]=="12" and r["idArticuloEstadistico"]=="99"
    assert r["route_description"] is None and r["planillaCarga"]=="0000 - 00000123"
    assert r["line_identity"] is None and r["commercial_included"]


def test_no_sheet_required_and_no_deposit_inference():
    r=normalize(sale(idDeposito=None,planillaCarga=""),capture_id="x",page=1,ordinal=0,captured_at="now")
    assert r["commercial_included"] and r["idDeposito"] is None and r["planillaCarga"]==""
    assert identifier(True) is None


def test_multiset_preserves_legitimate_identical_lines():
    a,b=sale(),sale(idDeposito=1)
    assert multiset_hash([a,b])==multiset_hash([b,a])
    assert multiset_hash([a,a])!=multiset_hash([a])


def test_reconciliation_detects_compensated_economic_changes():
    a=[{"date":"2026-09-28","invoice":"a","amount":10},{"date":"2026-09-28","invoice":"b","amount":20}]
    b=[{**a[0],"amount":11},{**a[1],"amount":19}]
    assert not reconcile(a,b)["consistent"]
    assert reconcile(a,a)["consistent"]
    assert not reconcile(a,a+[a[0]])["consistent"]


def test_units_fail_closed_and_negative_returns():
    assert unit_evidence([sale()],6)["observed_compatible"]
    assert unit_evidence([sale(cantidadesTotal=-2,cantidadSolicitada=-2)],6)["observed_compatible"]
    for rows,uxb in [([sale()],0),([sale(unidadesSolicitadas=None)],6),([sale(unidadesSolicitadas=1)],6)]:
        assert not unit_evidence(rows,uxb)["observed_compatible"]
    assert not unit_evidence([sale()],6,pesable=True)["observed_compatible"]
    # Measurement units (e.g. liters) must never be silently treated as pieces.
    assert unit_evidence([sale(unimedtotal=9)],6)["observed_compatible"]
    assert not unit_evidence([sale(cantidadSolicitada=0.5,cantidadesTotal=0.5)],6)["observed_compatible"]


def test_explicit_source_zero_is_preserved(tmp_path):
    c=Capture(tmp_path)
    c.get=lambda *a:({"dsReporteComprobantesApi":{},"cantComprobantesVentas":"Numero de lote obtenido: 1/1. Cantidad de comprobantes totales: 0"},{},"raw")
    result=c.day_pass("2026-09-28","x",1)
    assert result["empty"] and result["pages"][0]["explicit_zero"]


def test_capture_range_guard_and_atomic_checkpoint(tmp_path):
    c=Capture(tmp_path)
    with pytest.raises(ValueError):c.run(date(2022,1,1),date(2026,9,28))
    atomic_json(tmp_path/"test.json",{"status":"partial"})
    atomic_json(tmp_path/"test.json",{"status":"complete"})
    assert 'complete' in (tmp_path/"test.json").read_text()


def test_capture_does_not_stop_on_short_detail_page(tmp_path):
    c=Capture(tmp_path);called=[]
    def get(endpoint,params,destination):
        n=params["nroLote"];called.append(n)
        return {"dsReporteComprobantesApi":{"VentasResumen":[sale(nrodoc=n)]},"cantComprobantesVentas":f"{n}/2"},{"bytes":1},str(n)
    c.get=get
    p=c.day_pass("2026-09-28","x",1)
    assert p["rows"]==2 and called==[1,2]


def test_application_error_is_not_empty(tmp_path):
    c=Capture(tmp_path)
    c.get=lambda *a:({"error":[{"mensaje":"failed"}],"dsReporteComprobantesApi":{"VentasResumen":[]}}, {}, "")
    with pytest.raises(ValueError):c.day_pass("2026-09-28","x",1)


def test_unknown_empty_response_does_not_certify(tmp_path):
    c=Capture(tmp_path)
    c.get=lambda *a:({"dsReporteComprobantesApi":{}},{},"")
    with pytest.raises(ValueError):c.day_pass("2026-09-28","x",1)


def test_partial_source_cannot_load(tmp_path):
    from sales_coach.repositories.sales_v2_local_repository import LocalSalesV2Repository
    repo=object.__new__(LocalSalesV2Repository)
    with pytest.raises(ValueError):repo.load_day({"status":"failed"})


def test_native_storage_repeat_and_failed_insert_are_isolated(tmp_path,monkeypatch):
    from sales_coach.repositories.sales_v2_local_repository import LocalSalesV2Repository
    binary=Path.home()/'.local/share/intel-comercial-v2-pilot/bin/clickhouse'
    if not binary.exists():pytest.skip('Optional native ClickHouse pilot binary not installed')
    (tmp_path/'bin').mkdir();(tmp_path/'bin/clickhouse').symlink_to(binary)
    atomic_json(tmp_path/'preflight.json',{})
    raw=canonical_bytes=json.dumps({'dsReporteComprobantesApi':{'VentasResumen':[sale(),sale()]}}).encode()
    with gzip.open(tmp_path/'raw.gz','wb') as f:f.write(raw)
    from sales_coach.domain.sales_v2 import multiset_hash
    p={'path':'raw.gz','sha256':hashlib.sha256(raw).hexdigest(),'page':1,'rows':2,'finished_at':'2026-09-29T00:00:00Z'}
    one={'rows':2,'digest':multiset_hash([sale(),sale()]),'pages':[p]}
    entry={'day':'2026-09-28','status':'source_verified','capture_id':'source','passes':[one,one]}
    atomic_json(tmp_path/'days/2026-09-28.json',entry)
    repo=LocalSalesV2Repository(tmp_path);repo.initialize()
    original=repo.insert_file
    def fail_after_lines(table,path):
        original(table,path)
        if table=='fact_sales_lines_v2':raise RuntimeError('Simulated lost acknowledgement')
    monkeypatch.setattr(repo,'insert_file',fail_after_lines)
    with pytest.raises(RuntimeError):repo.load_day(entry)
    assert not (tmp_path/'selected/2026-09-28.json').exists()
    monkeypatch.setattr(repo,'insert_file',original)
    repo.load_available();repo.load_available()
    selected=json.loads((tmp_path/'selected/2026-09-28.json').read_text())
    assert selected['rows']==2
    assert repo.query(f"SELECT count() FROM fact_sales_lines_v2 WHERE load_id='{selected['load_id']}'").strip()=='2'
    assert repo.query('SELECT count() FROM fact_sales_lines_v2').strip()=='4'
    assert repo.query(f"SELECT uniqExact(idDeposito) FROM fact_sales_lines_v2 WHERE load_id='{selected['load_id']}'").strip()=='1'
