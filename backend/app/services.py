"""Application workflows. These functions modify a workspace inside Store.mutate."""
import csv
import io
import math
import re
from datetime import date
from uuid import uuid4
from .logic import overdue, suggest, timestamp, transaction_id, TRANSACTION_FIELDS
from .store import DATA


def log(state, text):
    state['activity'].insert(0, dict(id=str(uuid4()), text=text, at=timestamp()))


def message(direction, text):
    return dict(direction=direction, text=text, at=timestamp())


def conversation(state, identifier):
    result = next((c for c in state['conversations'] if c['id'] == identifier), None)
    if result is None:
        raise ValueError('Conversation not found')
    return result


def create_reminders(state):
    count = 0
    for invoice in state['invoices']:
        number = invoice['invoice_number']
        if not overdue(invoice) or any(c['invoice'] == number for c in state['conversations']):
            continue
        text = (f"Reminder: {number} has {invoice['currency']} {invoice['balance_due']:.2f} "
                f"outstanding, due {invoice['due_date']}. Please arrange payment.")
        state['conversations'].append(dict(id=str(uuid4()), customer_id=invoice['customer_id'],
            invoice=number, status='open', messages=[message('out', text)]))
        count += 1
    log(state, f'{count} demo reminders prepared')
    return dict(message=f'{count} demo reminders created. No real email sent.')


def process_reply(state, identifier, text):
    thread = conversation(state, identifier)
    if thread['status'] == 'attention':
        raise ValueError('Review the pending draft first.')
    thread['messages'].append(message('in', text))
    invoices = [i for i in state['invoices'] if i['customer_id'] == thread['customer_id']
                and i['status'] == 'sent' and i['balance_due'] > 0]
    answer = None
    if re.search(r'extension|more time|next month|instalment|installment|dispute|wrong|legal|another company', text, re.I):
        thread.update(status='attention', reason='Request requires Accounts Team review',
            draft='Thank you for your message. Our Accounts Team will review your request and follow up.')
    elif re.search(r'paid|will pay', text, re.I):
        answer = ('Thank you. We have recorded your payment update. Invoice balances remain unchanged '
                  'until the Accounts Team approves a bank receipt.')
    elif re.search(r'balance|owe|pending|outstanding|bank|pay|invoice', text, re.I):
        if invoices:
            summary = '; '.join(f"{i['invoice_number']}: {i['currency']} {i['balance_due']:.2f}, due {i['due_date']}"
                                for i in invoices)
            bank = state['organization']['bank_details']
            answer = (f"Your outstanding invoices: {summary}. Payment details: {bank['bank']}, "
                      f"account {bank['account_number']}, IFSC {bank['ifsc']}.")
        else:
            answer = 'You have no outstanding sent invoices.'
    else:
        thread.update(status='attention', reason='Demo rules cannot confidently answer this message',
                      draft='Thank you for contacting us. Our Accounts Team will review your question.')
    if answer:
        thread['messages'].append(message('out', answer))
    log(state, 'Demo customer reply processed')


def send_reviewed_reply(state, identifier, text):
    thread = conversation(state, identifier)
    if thread['status'] != 'attention':
        raise ValueError('No draft awaiting review')
    thread['messages'].append(message('out', text))
    thread['status'] = 'open'
    thread.pop('draft', None)
    thread.pop('reason', None)
    log(state, 'Reviewed draft saved as a demo response')


def import_statement(state, content, sample=False):
    if sample:
        content = (DATA / 'bank_statement_sample.csv').read_text(encoding='utf-8')
    if not isinstance(content, str):
        raise ValueError('CSV content required')
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')), skipinitialspace=True, strict=True)
        if reader.fieldnames is None or not set(TRANSACTION_FIELDS).issubset(reader.fieldnames):
            raise ValueError('CSV headers must match the supplied sample')
        rows = list(reader)
    except csv.Error as error:
        raise ValueError(f'Invalid CSV: {error}') from error
    if not 1 <= len(rows) <= 2000:
        raise ValueError('Upload 1–2,000 rows')
    added = 0
    for row in rows:
        if None in row or any(row.get(key) is None for key in TRANSACTION_FIELDS):
            raise ValueError('CSV row has an incorrect number of fields')
        transaction = {key: row[key].strip() for key in TRANSACTION_FIELDS}
        try:
            for key in ('credit', 'debit'):
                transaction[key] = float(transaction[key] or 0)
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', transaction['txn_date']):
                raise ValueError()
            date.fromisoformat(transaction['txn_date'])
            if (not all(math.isfinite(transaction[key]) and transaction[key] >= 0 for key in ('credit', 'debit'))
                or (transaction['credit'] > 0) == (transaction['debit'] > 0)
                or transaction['currency'] not in ('INR', 'USD', 'EUR', 'GBP')):
                raise ValueError()
        except ValueError as error:
            raise ValueError('Invalid date, currency or credit/debit amount') from error
        transaction['id'] = transaction_id(transaction)
        if any(existing['id'] == transaction['id'] for existing in state['transactions']):
            continue
        transaction['status'] = 'excluded' if transaction['debit'] else 'pending'
        transaction['suggestion'] = suggest(transaction, state['invoices'], state['customers'])
        state['transactions'].append(transaction)
        added += 1
    log(state, f'{added} bank transactions imported')
    return dict(message=f'{added} transactions imported; duplicates skipped.')
