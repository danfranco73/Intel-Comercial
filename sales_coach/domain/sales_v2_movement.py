"""Versioned experimental economic/physical contract; never imported by production."""
from decimal import Decimal
from sales_coach.domain.sales_v2 import number, identifier

VERSION = 'sales-v2-economic-physical-2'
RETURNS = {'DVVTA', 'PRDVO'}
ZERO = Decimal(0)


def sign(value):
    return None if value is None else (1 if value > 0 else -1 if value < 0 else 0)


def convert_parts(raw, uxb):
    """Integer pieces, with only the source's known fractional-pack rounding margin."""
    u = number(uxb)
    if not u or u <= 0 or u != u.to_integral_value():
        return None, ['UXB_UNVERIFIED']
    fields = ('cantidadesCorCargo', 'cantidadesSinCargo', 'cantidadesTotal', 'cantidadSolicitada', 'unidadesSolicitadas')
    q = {k:number(raw.get(k)) for k in fields}
    if any(v is None for v in q.values()):
        return None, ['PHYSICAL_QUANTITY_MISSING']
    if number(raw.get('presentacionArticulo')) != u:
        return None, ['PRESENTATION_MISMATCH']
    tolerance = max(Decimal('0.000001'), u*Decimal('0.0000005'))
    total = q['cantidadSolicitada']*u+q['unidadesSolicitadas']
    if any(q[k] != q[k].to_integral_value() for k in ('cantidadSolicitada','unidadesSolicitadas')):
        return None, ['NONINTEGER_PACKS_OR_LOOSE']
    charged = (q['cantidadesCorCargo']*u).to_integral_value()
    free = (q['cantidadesSinCargo']*u).to_integral_value()
    if abs(q['cantidadesCorCargo']*u-charged)>tolerance or abs(q['cantidadesSinCargo']*u-free)>tolerance:
        return None, ['NONINTEGER_CHARGED_OR_FREE_PIECES']
    if abs(q['cantidadesTotal']*u-total)>tolerance or charged+free!=total:
        return None, ['CHARGED_FREE_TOTAL_MISMATCH']
    return {'charged':charged,'free':free,'total':total}, []


def model(raw, *, uxb=None, unit_approved=False):
    """Preserve explicit economic zeros and signed physical quantities independently."""
    cancelled = str(raw.get('anulado','')).strip().upper()=='SI'
    economy = {name:number(raw.get(source)) for name,source in (
        ('gross','subtotalBruto'),('discount','subtotalBonificado'),('discount_percent','bonificacion'),
        ('net','subtotalNeto'),('final','subtotalFinal'))}
    parts, reasons = convert_parts(raw, uxb) if unit_approved else (None,['UNIT_UNVERIFIED'])
    if economy['discount_percent']==100 and (economy['net']!=0 or economy['final']!=0):
        reasons.append('FULL_DISCOUNT_ECONOMIC_CONFLICT')
    total = parts['total'] if parts else None
    return {'version':VERSION,'document_type':raw.get('idDocumento'),'cancelled':cancelled,
        'deposit_id':identifier(raw.get('idDeposito')),'physical_article_id':identifier(raw.get('idArticulo')),
        'economic':{**economy,'net_sign':sign(economy['net'])},
        'physical':{'charged_original':number(raw.get('cantidadesCorCargo')),
            'free_original':number(raw.get('cantidadesSinCargo')),'total_original':number(raw.get('cantidadesTotal')),
            'original_measure_total':number(raw.get('unimedtotal')),
            'original_unit':raw.get('idUnidadMedida'), 'original_unit_label':raw.get('dsUnidadMedida'),
            'presentation_original':raw.get('presentacionArticulo'),
            'uxb_validated':number(uxb) if unit_approved else None,
            'pieces':parts,'sign':sign(total),'included':not cancelled and parts is not None,
            'outbound':max(total,ZERO) if total is not None else None,
            'returns':-min(total,ZERO) if total is not None and raw.get('idDocumento') in RETURNS else ZERO if total is not None else None,
            'other_negative':-min(total,ZERO) if total is not None and raw.get('idDocumento') not in RETURNS else ZERO if total is not None else None},
        'issues':reasons}


def window_result(outbound, returns, other_negative, stock, days):
    net = outbound-returns-other_negative
    average = net/Decimal(days)
    return {'outbound':outbound,'returns':returns,'other_negative':other_negative,'net':net,
        'daily_average':average,'theoretical_coverage_days':stock/average if stock is not None and stock>=0 and average>0 else None}


def stock_observations(stock, activity_30, activity_60, deposit_id):
    """Factual, overlapping observations; no severity or arbitrary thresholds."""
    if stock is None:
        return []
    result = []
    if stock == 0 and activity_60 > 0:
        result.append('ZERO_STOCK_WITH_HISTORY')
    if stock < 0:
        result.append('NEGATIVE_STOCK')
    if stock > 0 and activity_30 == 0:
        result.append('POSITIVE_NO_MOVEMENT_30')
    if stock > 0 and activity_60 == 0:
        result.append('POSITIVE_NO_MOVEMENT_60')
    if str(deposit_id) == '5' and stock != 0:
        result.append('TRANSIENT_NONZERO_BALANCE')
    return result
