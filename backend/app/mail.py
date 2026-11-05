"""Explicit, single-recipient SMTP test. No automatic or bulk sending."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from uuid import uuid4
from dotenv import dotenv_values
from .store import BACKEND
from .logic import overdue, timestamp
from .services import log


def send_test_reminder(store, invoice_number):
    settings = dotenv_values(BACKEND / '.env')
    sender = (settings.get('MAIL_USERNAME') or '').strip()
    recipient = (settings.get('TEST_PAYER_EMAIL') or '').strip()
    password = ''.join((settings.get('MAIL_APP_PASSWORD') or '').split())
    if not all((sender, recipient, password)):
        raise ValueError('Set MAIL_USERNAME, MAIL_APP_PASSWORD and TEST_PAYER_EMAIL in backend/.env.')
    if len(password) != 16:
        raise ValueError('MAIL_APP_PASSWORD must contain the 16-character Google App Password, not the normal account password.')
    if any('\n' in address or '\r' in address or '@' not in address for address in (sender, recipient)):
        raise ValueError('Invalid configured email address.')
    if sender.lower() == recipient.lower():
        raise ValueError('Use a separate payer test inbox.')
    # For this Gmail test, use fixed verified endpoints rather than arbitrary destinations.
    if settings.get('SMTP_HOST', 'smtp.gmail.com') != 'smtp.gmail.com':
        raise ValueError('This test sender is configured for Gmail only.')
    identifier, message_id = str(uuid4()), make_msgid()

    def reserve(state):
        invoice = next((i for i in state['invoices'] if i['invoice_number'] == invoice_number), None)
        if invoice is None or not overdue(invoice):
            raise ValueError('The selected invoice is not currently overdue and unpaid.')
        from .availability import pause_active
        paused_customer=next(c for c in state['customers'] if c['customer_id']==invoice['customer_id'])
        if pause_active(paused_customer):raise ValueError('Out of office; reminders paused until '+paused_customer['reminder_pause']['resume_on'])
        if any(m.get('to','').lower()==recipient.lower() and m.get('delivery_notice')=='delivery_failed' for t in state['conversations'] for m in t['messages']):
            raise ValueError('A delivery failure was reported for this address; correct it before sending.')
        for thread in state['conversations']:
            if thread['invoice'] == invoice_number and any(
                m.get('transport') == 'smtp' and m.get('to') == recipient
                and m.get('delivery_status') in ('sending', 'accepted', 'unknown')
                for m in thread['messages']
            ):
                raise ValueError('A real reminder already exists or its delivery is uncertain. Check the inbox before sending again.')
        customer = next(c for c in state['customers'] if c['customer_id'] == invoice['customer_id'])
        subject = f"[Assignment test] Payment reminder - {invoice_number}"
        body = (f"Dear {customer['contact_name']},\n\n"
                f"This is a reminder that invoice {invoice_number} for {invoice['currency']} "
                f"{invoice['balance_due']:,.2f}, due on {invoice['due_date']}, remains outstanding.\n\n"
                "Please let us know your payment status or reply with any questions.\n\n"
                "Regards,\nAcmeCorp Accounts Team\n\n"
                "Assignment test only: this invoice and company are fictional. No actual payment is requested.")
        customer['contact_email'] = recipient
        state['organization']['accounts_email'] = sender
        state['conversations'].append(dict(id=identifier, customer_id=customer['customer_id'],
            invoice=invoice_number, status='open', messages=[dict(direction='out', text=body,
            at=timestamp(), transport='smtp', delivery_status='sending', message_id=message_id,
            subject=subject, to=recipient, sender=sender)]))
        return subject, body

    subject, body = store.mutate(reserve)
    email = EmailMessage()
    email['From'], email['To'], email['Subject'] = sender, recipient, subject
    email['Message-ID'], email['Date'] = message_id, formatdate(localtime=False)
    email.set_content(body)
    phase = 'connect'
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(sender, password)
            phase = 'send'
            refused = smtp.send_message(email)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
            phase = 'accepted'
    except Exception as error:
        # Never expose credentials or raw server exceptions to API clients/logs.
        if phase == 'accepted':
            delivery = 'accepted'  # A QUIT failure does not undo server acceptance.
        else:
            delivery = 'unknown' if phase == 'send' else 'failed'
        if isinstance(error, smtplib.SMTPAuthenticationError):
            problem = 'Gmail rejected authentication. Check the company App Password and 2-Step Verification.'
        else:
            problem = f'SMTP connection failed ({type(error).__name__}).' if phase == 'connect' else 'Delivery is uncertain; check Sent and the payer inbox before retrying.'
    else:
        delivery, problem = 'accepted', None

    def finish(state):
        thread = next(c for c in state['conversations'] if c['id'] == identifier)
        thread['messages'][0]['delivery_status'] = delivery
        if delivery != 'accepted':
            thread.update(status='attention', reason=problem, draft='')
        log(state, f'SMTP test reminder for {invoice_number}: {delivery}')
    store.mutate(finish)
    if delivery != 'accepted':
        raise ValueError(problem)
    return dict(message='Gmail accepted one test reminder for delivery.', recipient=recipient,
                invoice=invoice_number, message_id=message_id, conversation_id=identifier)
