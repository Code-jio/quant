"""Position episodes: a trade closes when a symbol/side returns to zero.

Close fill pnl is net of its own fee; allocate entry fees proportionally.
Legacy fills with no offset are inferred by direction, but new fills carry offsets.
"""
from collections import defaultdict


def completed_trades(trades):
    books = defaultdict(lambda: {'volume':0, 'entry_fees':0., 'pnl':0.})
    result = []
    for raw in trades:
        get = raw.get if isinstance(raw,dict) else lambda k,d=None: getattr(raw,k,d)
        side = getattr(get('direction'), 'value', get('direction'))
        offset = getattr(get('offset'), 'value', get('offset'))
        symbol = get('symbol')
        opposite = 'short' if side == 'long' else 'long'
        closing = offset not in (None,'open') or (offset is None and books[(symbol,opposite)]['volume'] > 0)
        volume = abs(int(get('volume',0)))
        if not volume:
            continue
        book = books[(symbol,opposite if closing else side)]
        if not closing:
            book['volume'] += volume
            book['entry_fees'] += float(get('commission',0) or 0)
            continue
        if book['volume'] < volume:
            # An incomplete import cannot be claimed as a complete trade.
            continue
        allocated = book['entry_fees'] * volume / book['volume']
        book['entry_fees'] -= allocated
        book['volume'] -= volume
        book['pnl'] += float(get('pnl',0) or 0) - allocated
        if not book['volume']:
            result.append({'symbol':symbol,'pnl':book['pnl']})
            books.pop((symbol,opposite))
    return result


def finite_json(value):
    import math
    from numbers import Real
    if isinstance(value,dict):
        return {k:finite_json(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):
        return [finite_json(v) for v in value]
    if isinstance(value,Real) and not math.isfinite(value):
        return None
    return value
