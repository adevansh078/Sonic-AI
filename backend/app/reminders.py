"""Preview and send one consolidated reminder for a selected customer."""
import hashlib
import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from uuid import uuid4
from dotenv import dotenv_values
from .store import BACKEND
from .logic import overdue, timestamp, cents


def preview(state, customer_id, config, invoice_numbers=None):
    customer = next((c for c in state['customers'] if c['customer_id'] == customer_id), None)
    if customer is None:
        raise ValueError('Customer not found.')
    invoices = sorted((i for i in state['invoices'] if i['customer_id'] == customer_id
                       and i['status'] == 'sent' and i['balance_due'] > 0),
                      key=lambda i: (i['due_date'], i['invoice_number']))
    if not invoices:
        raise ValueError('This customer has no outstanding sent invoices.')
    subset = False
    if invoice_numbers is not None:
        chosen = set(invoice_numbers)
        eligible = {i['invoice_number'] for i in invoices}
        if not chosen or len(chosen)!=len(invoice_numbers) or not chosen.issubset(eligible):
            raise ValueError('Select distinct, sent unpaid invoices belonging to this customer. Refresh if balances changed.')
        subset = chosen != eligible
        invoices = [i for i in invoices if i['invoice_number'] in chosen]
    sender = (config.get('MAIL_USERNAME') or '').strip()
    recipient = customer['contact_email'].strip()
    subject = 'Payment reminder - outstanding invoices'
    lines = [f"Dear {customer['contact_name']},", '',
             'Please find your outstanding invoices with AcmeCorp below:', '']
    totals = {}
    for i in invoices:
        totals[i['currency']] = totals.get(i['currency'], 0) + cents(i['balance_due'])
        lines.append(f"{i['invoice_number']}: {i['currency']} {i['balance_due']:,.2f} outstanding; "
                     f"due {i['due_date']} ({'overdue' if overdue(i) else 'not yet overdue'}).")
    lines.append('')
    total_label = 'Total for invoices in this reminder' if subset else 'Total outstanding'
    lines.extend(f'{total_label} ({currency}): {amount / 100:,.2f}.' for currency, amount in sorted(totals.items()))
    lines.extend(['', 'Please arrange payment for overdue amounts and let us know your payment status. '
                  'Amounts not yet due are included for your reference. Reply with any questions.',
                  '', 'Regards,', 'AcmeCorp Accounts Team', '',
                  'Assignment test only: these invoices and companies are fictional. No actual payment is requested.'])
    result = dict(customer_id=customer_id, customer_name=customer['name'], sender=sender,
                  recipient=recipient, subject=subject, body='\n'.join(lines),
                  invoice_numbers=[i['invoice_number'] for i in invoices])
    result['preview_id'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    blocked = None
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', recipient) or not sender:
        blocked = 'Configure a valid customer email and company mailbox.'
    elif recipient.lower() != (config.get('TEST_PAYER_EMAIL') or '').strip().lower():
        blocked = 'For this test, set this customer’s email to TEST_PAYER_EMAIL on the Customers page.'
    elif sender.lower() == recipient.lower():
        blocked = 'The company and payer must use different inboxes.'
    elif len(''.join((config.get('MAIL_APP_PASSWORD') or '').split())) != 16:
        blocked = 'Configure the company Gmail App Password.'
    elif any(m.get('reminder_preview_id') == result['preview_id']
             and m.get('delivery_status') in ('sending', 'accepted', 'unknown')
             for t in state['conversations'] for m in t['messages']):
        blocked = 'This reminder was already sent or its delivery is uncertain. Check the conversation before retrying.'
    from .availability import pause_active
    if pause_active(customer):
        blocked = 'Out of office. Reminders resume '+customer['reminder_pause']['resume_on']+' (Asia/Kolkata).'
    if any(m.get('to','').lower()==recipient.lower() and m.get('delivery_notice')=='delivery_failed' for t in state['conversations'] for m in t['messages']):
        blocked='A delivery failure was reported for this address. Correct the customer email before sending another reminder.'
    result['blocked_reason'] = blocked
    return result


def send(store, customer_id, preview_id, invoice_numbers=None):
    config = dotenv_values(BACKEND / '.env')
    identifier, mid = str(uuid4()), make_msgid()

    def reserve(state):
        prepared = preview(state, customer_id, config, invoice_numbers)
        if prepared['preview_id'] != preview_id:
            raise ValueError('The recipient or invoice details changed. Preview the reminder again.')
        if prepared['blocked_reason']:
            raise ValueError(prepared['blocked_reason'])
        state['conversations'].append(dict(id=identifier, customer_id=customer_id,
            invoice=', '.join(prepared['invoice_numbers']), invoice_numbers=prepared['invoice_numbers'],
            status='open', messages=[dict(direction='out', text=prepared['body'], at=timestamp(),
                transport='smtp', delivery_status='sending', message_id=mid,
                reminder_preview_id=preview_id, subject=prepared['subject'],
                to=prepared['recipient'], sender=prepared['sender'])]))
        return prepared

    prepared = store.mutate(reserve)
    mail = EmailMessage()
    mail['From'], mail['To'], mail['Subject'] = prepared['sender'], prepared['recipient'], prepared['subject']
    mail['Message-ID'], mail['Date'] = mid, formatdate()
    mail.set_content(prepared['body'])
    phase = 'connect'
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as smtp:
            smtp.ehlo(); smtp.starttls(context=ssl.create_default_context()); smtp.ehlo()
            smtp.login(prepared['sender'], ''.join(config['MAIL_APP_PASSWORD'].split()))
            phase = 'send'
            if smtp.send_message(mail):
                raise ValueError('Recipient refused')
            phase = 'accepted'
        outcome = 'accepted'
    except Exception:
        outcome = 'accepted' if phase == 'accepted' else 'unknown' if phase == 'send' else 'failed'

    def finish(state):
        thread = next(t for t in state['conversations'] if t['id'] == identifier)
        thread['messages'][0]['delivery_status'] = outcome
        if outcome != 'accepted':
            thread.update(status='attention', reason=f'SMTP {outcome}; check delivery before retrying.', draft=prepared['body'])
    store.mutate(finish)
    if outcome != 'accepted':
        raise ValueError(f'Reminder delivery {outcome}. See Needs attention for details.')
    return dict(message='One consolidated reminder accepted by Gmail for delivery.', conversation_id=identifier)
