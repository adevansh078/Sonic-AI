"""Pure invoice and reconciliation rules, independent of FastAPI."""
import hashlib
import json
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def timestamp():
    return datetime.now(ZoneInfo('UTC')).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def cents(value):
    # Match the original JavaScript rounding for non-negative financial amounts.
    return math.floor(float(value) * 100 + 0.5)


def overdue(invoice, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()
    return (invoice['status'] == 'sent' and invoice['balance_due'] > 0
            and invoice['due_date'] < today)


def tokens(text):
    ignored = {'pvt', 'ltd', 'private', 'limited', 'neft', 'rtgs', 'swift', 'imps'}
    return {word for word in re.sub(r'[^a-z0-9]', ' ', str(text).lower()).split()
            if len(word) > 2 and word not in ignored}


def suggest(transaction, invoices, customers=None):
    from .matching import match
    return match(transaction, invoices, customers)


TRANSACTION_FIELDS = ('txn_date', 'narration', 'reference', 'credit', 'debit', 'currency')


def transaction_id(transaction):
    # Preserve JS JSON.stringify ordering and integral-number formatting for old imports.
    row = {key: transaction[key] for key in TRANSACTION_FIELDS}
    for key in ('credit', 'debit'):
        if float(row[key]).is_integer():
            row[key] = int(row[key])
    serialized = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(serialized.encode()).hexdigest()[:20]


def approve(state, transaction_id, invoice_number, mode):
    transaction = next((t for t in state['transactions'] if t['id'] == transaction_id), None)
    invoice = next((i for i in state['invoices'] if i['invoice_number'] == invoice_number), None)
    if not transaction or transaction['status'] != 'pending':
        raise ValueError('Transaction is unavailable or already reviewed.')
    if not invoice or not (invoice['status'] == 'sent' and invoice['balance_due'] > 0
            and transaction['credit'] and invoice['currency'] == transaction['currency']
            and invoice['invoice_date'] <= transaction['txn_date']):
        raise ValueError('Choose an eligible unpaid invoice with matching currency and valid dates.')
    receipt, balance = cents(transaction['credit']), cents(invoice['balance_due'])
    if receipt > balance:
        raise ValueError('Overpayment requires manual handling; choose another invoice.')
    adjustment = 0
    if mode == 'tds':
        if invoice['currency'] != 'INR' or balance - receipt != cents(invoice['subtotal'] * .1):
            raise ValueError('TDS closure requires an INR shortfall equal to 10% of subtotal.')
        adjustment = balance - receipt
    invoice['amount_paid'] = (cents(invoice['amount_paid']) + receipt) / 100
    invoice['tds_adjustment'] = (cents(invoice.get('tds_adjustment', 0)) + adjustment) / 100
    invoice['balance_due'] = (balance - receipt - adjustment) / 100
    if invoice['balance_due'] == 0:
        invoice['status'] = 'paid'
    transaction.update(status='approved', approved_invoice=invoice_number)
    state['payments'].append(dict(id=transaction['id'], invoice_number=invoice_number,
        cash=receipt / 100, tds=adjustment / 100, currency=invoice['currency'], approved_at=timestamp()))
