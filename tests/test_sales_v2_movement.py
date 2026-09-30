from decimal import Decimal as D
from sales_coach.domain.sales_v2_movement import model, window_result


def row(**changes):
    r=dict(idDocumento='FCVTA',idDeposito=5,idArticulo=10,anulado='NO',subtotalBruto=100,
        subtotalBonificado=100,bonificacion=100,subtotalNeto=0,subtotalFinal=0,
        cantidadesCorCargo=0,cantidadesSinCargo=1,cantidadesTotal=1,cantidadSolicitada=1,
        unidadesSolicitadas=0,presentacionArticulo=12,unimedtotal=6)
    r.update(changes);return r


def test_free_goods_zero_revenue_positive_physical_and_transient_deposit():
    m=model(row(),uxb=12,unit_approved=True)
    assert m['economic']['net']==0 and m['physical']['pieces']['free']==12
    assert m['physical']['included'] and m['physical']['outbound']==12 and m['deposit_id']=='5'
    assert m['physical']['original_measure_total']==6


def test_free_return_keeps_zero_revenue_and_negative_physical():
    m=model(row(idDocumento='DVVTA',subtotalBruto=-100,subtotalBonificado=-100,
        cantidadesSinCargo=-1,cantidadesTotal=-1,cantidadSolicitada=-1),uxb=12,unit_approved=True)
    assert m['economic']['net']==0 and m['physical']['pieces']['total']==-12
    assert m['physical']['returns']==12 and m['physical']['outbound']==0


def test_missing_net_is_not_replaced_with_gross_and_quantity_survives():
    m=model(row(subtotalNeto=None,bonificacion=0),uxb=12,unit_approved=True)
    assert m['economic']['net'] is None and m['physical']['included']


def test_cancelled_is_preserved_but_not_counted():
    m=model(row(anulado='SI'),uxb=12,unit_approved=True)
    assert not m['physical']['included'] and m['physical']['pieces']['total']==12


def test_negative_invoice_adjustment_is_not_labelled_credit_return():
    m=model(row(cantidadesSinCargo=-1,cantidadesTotal=-1,cantidadSolicitada=-1),uxb=12,unit_approved=True)
    assert m['physical']['other_negative']==12 and m['physical']['returns']==0


def test_component_mismatch_blocks_physical_conversion():
    m=model(row(cantidadesCorCargo=1),uxb=12,unit_approved=True)
    assert not m['physical']['included'] and 'CHARGED_FREE_TOTAL_MISMATCH' in m['issues']


def test_fractional_pack_converts_one_piece_without_using_volume_unit():
    m=model(row(cantidadesSinCargo=0.0833333333,cantidadesTotal=0.0833333333,
        cantidadSolicitada=0,unidadesSolicitadas=1),uxb=12,unit_approved=True)
    assert m['physical']['pieces']['free']==1


def test_net_zero_and_negative_stock_have_no_fabricated_coverage():
    assert window_result(D(12),D(12),D(0),D(5),30)['theoretical_coverage_days'] is None
    assert window_result(D(12),D(0),D(0),D(-5),30)['theoretical_coverage_days'] is None
    assert window_result(D(12),D(0),D(0),D(0),30)['theoretical_coverage_days']==0


def test_transient_balance_is_unknown_when_missing_and_observed_when_nonzero():
    from sales_coach.domain.sales_v2_movement import stock_observations
    assert stock_observations(None,D(0),D(0),'5')==[]
    assert 'TRANSIENT_NONZERO_BALANCE' in stock_observations(D(1),D(1),D(1),'5')
    assert 'TRANSIENT_NONZERO_BALANCE' in stock_observations(D(-1),D(1),D(1),'5')
    assert 'TRANSIENT_NONZERO_BALANCE' not in stock_observations(D(0),D(1),D(1),'5')


def test_offsetting_physical_activity_is_not_no_movement():
    from sales_coach.domain.sales_v2_movement import stock_observations
    assert stock_observations(D(5),D(24),D(24),'1')==[]
    assert stock_observations(D(5),D(0),D(24),'1')==['POSITIVE_NO_MOVEMENT_30']
