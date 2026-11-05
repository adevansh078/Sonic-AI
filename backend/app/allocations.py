"""Bounded subset search and atomic, human-approved multi-invoice settlement."""
from .logic import cents, timestamp


def referenced_allocations(invoices, receipt, limit=100000):
    """Include every reference; enumerate closures plus at most one residual partial."""
    if len(invoices)>18:
        return [], True
    target, visited, limited = cents(receipt), 0, False
    answers, seen = [], set()
    def save(rows):
        nonlocal limited
        rows = sorted(rows, key=lambda r:r['invoice'])
        key = tuple((r['invoice'], r['cash'], r['tds']) for r in rows)
        if key not in seen:
            if len(answers)>=20:
                limited = True
                return
            seen.add(key); answers.append(rows)
    def row(i, cash, tds):
        return dict(invoice=i['invoice_number'], cash=cash/100, tds=tds/100,
                    balance_due=i['balance_due'], remaining=(cents(i['balance_due'])-cash-tds)/100)
    def search(pool, index, remaining, rows, partial):
        nonlocal visited, limited
        visited += 1
        if visited>limit:
            limited=True
        if limited:return
        if index==len(pool):
            if partial is None and remaining==0: save(rows)
            elif partial is not None and 0<remaining<cents(partial['balance_due']):
                save(rows+[row(partial,remaining,0)])
            return
        i=pool[index]; balance=cents(i['balance_due']); options=[(balance,0)]
        tds=cents(i['subtotal']*.1)
        if i['currency']=='INR' and 0<tds<balance and not i.get('tds_adjustment',0):
            options.append((balance-tds,tds))
        for cash,tds in options:
            if cash<=remaining:search(pool,index+1,remaining-cash,rows+[row(i,cash,tds)],partial)
    search(invoices,0,target,[],None)
    for partial in invoices:
        search([i for i in invoices if i is not partial],0,target,[],partial)
        if limited:break
    return answers, limited


def combinations(invoices, receipt, limit=100000):
    if len(invoices)>18:
        return [], True
    target = cents(receipt)
    answers, visited = [], 0
    def search(index, remaining, rows):
        nonlocal visited
        visited += 1
        if visited>limit or len(answers)>=20:
            return
        if remaining==0:
            if rows: answers.append(list(rows))
            return
        if index==len(invoices): return
        i = invoices[index]
        balance = cents(i['balance_due'])
        options = [(balance, 0)]
        tds = cents(i['subtotal']*.1)
        if i['currency']=='INR' and 0<tds<balance and not i.get('tds_adjustment', 0):
            options.append((balance-tds, tds))
        search(index+1, remaining, rows)
        for cash, adjustment in options:
            if cash<=remaining:
                search(index+1, remaining-cash, rows+[dict(invoice=i['invoice_number'],
                    cash=cash/100, tds=adjustment/100, balance_due=balance/100)])
    search(0, target, [])
    return answers, visited>limit or len(answers)>=20


def approve_allocation(state, identifier, rows):
    transaction = next((t for t in state['transactions'] if t['id']==identifier), None)
    if not transaction or transaction['status']!='pending':
        raise ValueError('Transaction unavailable or already reviewed.')
    if len(rows)<2 or len(rows)>18 or len({r['invoice'] for r in rows})!=len(rows):
        raise ValueError('Choose distinct invoices for the combined payment.')
    prepared, customers = [], set()
    for row in rows:
        i = next((i for i in state['invoices'] if i['invoice_number']==row['invoice']), None)
        if not i or i['status']!='sent' or i['balance_due']<=0 or i['currency']!=transaction['currency'] or i['invoice_date']>transaction['txn_date']:
            raise ValueError('Invoice changed or is ineligible; refresh the suggestions.')
        cash, tds = cents(row['cash']), cents(row['tds'])
        if row.get('balance_due') is not None and cents(row['balance_due'])!=cents(i['balance_due']):
            raise ValueError('Invoice balance changed; refresh the suggestions.')
        if cash<=0 or tds<0 or cash+tds>cents(i['balance_due']):
            raise ValueError('Allocation cannot exceed the current invoice balance.')
        if tds and cash+tds!=cents(i['balance_due']):
            raise ValueError('TDS closure must settle the invoice; partial payments must leave TDS at zero.')
        if tds and (i['currency']!='INR' or i.get('tds_adjustment',0) or tds!=cents(i['subtotal']*.1)):
            raise ValueError('Invalid TDS adjustment.')
        customers.add(i['customer_id']); prepared.append((i,cash,tds))
    if len(customers)!=1 or sum(c for _,c,_ in prepared)!=cents(transaction['credit']):
        raise ValueError('Allocation must belong to one customer and exactly equal the receipt.')
    # All validation precedes mutation. Caller holds Store.mutate's transaction lock.
    for i,cash,tds in prepared:
        remaining=cents(i['balance_due'])-cash-tds
        i.update(amount_paid=(cents(i['amount_paid'])+cash)/100,
                 tds_adjustment=(cents(i.get('tds_adjustment',0))+tds)/100,
                 balance_due=remaining/100, status='paid' if remaining==0 else 'sent')
        state['payments'].append(dict(id=identifier+':'+i['invoice_number'], transaction_id=identifier,
            invoice_number=i['invoice_number'], cash=cash/100, tds=tds/100,
            currency=i['currency'], approved_at=timestamp()))
    transaction.update(status='approved', approved_invoice=', '.join(r['invoice'] for r in rows),
                       approved_allocations=rows)
