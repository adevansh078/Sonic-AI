"""Collections orchestration: context, routing, factual rendering and durable sending."""
import json
import threading
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from dotenv import dotenv_values
from .store import BACKEND
from .logic import timestamp
from .llm import classify
from .inbox import fetch_new
from .review import recover_legacy, human_block

INTRO = {
    'balance_question': 'Thank you for your message. Please find your current invoice information below.',
    'invoice_question': 'Thank you for your message. Please find your current invoice information below.',
    'payment_instructions': 'Thank you for your message. Please find our payment details below.',
    'payment_claim': 'Thank you. We have recorded your payment update. Our Accounts Team will verify any payment against bank records.',
    'payment_promise': 'Thank you. We have recorded your payment update. Our Accounts Team will verify any payment against bank records.'}


def context_for(state, thread, incoming):
    customer = next(c for c in state['customers'] if c['customer_id']==thread['customer_id'])
    return {'organization': state['organization'], 'customer': {'name':customer['name'],'contact_name':customer['contact_name']},
        'invoices':[i for i in state['invoices'] if i['customer_id']==thread['customer_id'] and i['status']=='sent' and i['balance_due']>0],
        'history':[{'direction':m['direction'],'text':m['text']} for m in thread['messages'][-10:] if m is not incoming],
        'latest_email': incoming['text'], 'email_received_at':incoming.get('at'), 'timezone':'Asia/Kolkata', 'today':timestamp()[:10]}


def render(decision, context):
    if decision.requires_human_review or decision.intent not in INTRO or decision.confidence<.9:
        raise ValueError(decision.reason or 'LLM requested human review.')
    valid = {i['invoice_number'] for i in context['invoices']}
    if not set(decision.invoice_numbers).issubset(valid):
        raise ValueError('LLM referenced an ineligible invoice.')
    if decision.draft.strip()!=INTRO[decision.intent]:
        raise ValueError('Draft failed the approved-language check; human review required.')
    if decision.intent in ('balance_question','invoice_question') and not decision.include_balance:
        raise ValueError('Invoice question requires verified invoice information.')
    if decision.intent=='payment_instructions' and not decision.include_bank_details:
        raise ValueError('Payment instructions require verified bank details.')
    lines = [decision.draft]
    if decision.include_balance:
        totals = {}
        for i in context['invoices']:
            lines.append(f"{i['invoice_number']}: {i['currency']} {i['balance_due']:,.2f}, due {i['due_date']}.")
            totals[i['currency']] = totals.get(i['currency'],0)+round(i['balance_due']*100)
        lines.extend(f'Total outstanding: {currency} {amount/100:,.2f}.' for currency,amount in totals.items())
        if not totals: lines.append('You have no outstanding sent invoices.')
    if decision.include_bank_details:
        bank = context['organization']['bank_details']
        lines.append(f"Payee: {bank['account_name']}; bank: {bank['bank']}; account: {bank['account_number']}; IFSC: {bank['ifsc']}; SWIFT: {bank['swift_code']}.")
    return '\n\n'.join(lines)+'\n\nRegards,\nAcmeCorp Accounts Team'


def deliver(store, thread_id, incoming_id, text):
    config = dotenv_values(BACKEND / '.env')
    sender = config.get('MAIL_USERNAME','').strip()
    allowed = config.get('TEST_PAYER_EMAIL','').strip().lower()
    mid = make_msgid()
    def reserve(state):
        thread = next(c for c in state['conversations'] if c['id']==thread_id)
        incoming = next(m for m in thread['messages'] if m.get('id')==incoming_id)
        if incoming.get('no_reply') or incoming.get('message_category','').startswith('delivery_'):
            raise ValueError('Delivery notifications cannot receive email replies.')
        if not incoming.get('verified') or incoming['sender'].lower()!=allowed:
            raise ValueError('Sending is limited to the verified configured payer test inbox.')
        if any(m.get('reply_to_id')==incoming_id and m.get('delivery_status') in ('sending','accepted','unknown') for m in thread['messages']):
            raise ValueError('Response already sent or delivery uncertain; do not retry blindly.')
        subject = incoming.get('subject','Invoice inquiry')
        subject = subject if subject.lower().startswith('re:') else 'Re: '+subject
        thread['messages'].append(dict(direction='out',text=text,transport='smtp',delivery_status='sending',
            message_id=mid,reply_to_id=incoming_id,at=timestamp(),to=incoming['sender'],sender=sender))
        incoming['processing_status']='sending'
        return incoming['sender'], subject, incoming.get('message_id'), incoming.get('references',[])
    recipient,subject,parent,refs = store.mutate(reserve)
    message = EmailMessage()
    message['From'],message['To'],message['Subject'] = sender,recipient,subject
    message['Message-ID'],message['Date'] = mid,formatdate()
    if parent:
        message['In-Reply-To']=parent
        message['References']=' '.join((refs+[parent])[-20:])
    message['Auto-Submitted']='auto-replied'
    message.set_content(text)
    phase = 'connect'
    try:
        with smtplib.SMTP('smtp.gmail.com',587,timeout=25) as smtp:
            smtp.ehlo(); smtp.starttls(context=ssl.create_default_context()); smtp.ehlo()
            smtp.login(sender,''.join(config.get('MAIL_APP_PASSWORD','').split()))
            phase='send'
            smtp.send_message(message)
            phase='accepted'
        outcome='accepted'
    except Exception:
        outcome='accepted' if phase=='accepted' else 'unknown' if phase=='send' else 'failed'
    def finish(state):
        thread=next(c for c in state['conversations'] if c['id']==thread_id)
        outgoing=next(m for m in thread['messages'] if m.get('message_id')==mid)
        incoming=next(m for m in thread['messages'] if m.get('id')==incoming_id)
        outgoing['delivery_status']=outcome
        incoming['processing_status']='completed' if outcome=='accepted' else 'attention'
        if outcome=='accepted':
            thread.update(status='open');thread.pop('draft',None);thread.pop('reason',None)
        else: thread.update(status='attention',reason=f'SMTP {outcome}; inspect delivery before retrying.',draft=text)
    store.mutate(finish)
    if outcome!='accepted': raise ValueError(f'SMTP {outcome}; no automatic retry.')


class Agent:
    def __init__(self,store):
        self.store=store;self.lock=threading.Lock();self.stop_event=threading.Event();self.worker=None
        self.store.mutate(lambda s: [recover_legacy(t) for t in s['conversations']])
        # Repair only previously classified OOO messages rejected by the old date parser.
        def recover_dates(state):
            from .llm import Decision
            from .availability import apply_pause
            from .conversation_view import prepare_conversation
            for t in state['conversations']:
                for m in t['messages']:
                    if m.get('processing_status')!='attention' or m.get('llm_decision',{}).get('intent')!='out_of_office':continue
                    if m.get('review_reason') not in ('Use human review for relative, missing-year or ambiguous availability dates.','Return-day meaning is unclear; confirm when reminders may resume.'):continue
                    try:apply_pause(state,t['id'],m['id'],Decision(**m['llm_decision']))
                    except ValueError:continue
                    m.update(processing_status='completed',message_category='out_of_office',review_kind=None,review_reason=None)
                prepare_conversation(t)
        self.store.mutate(recover_dates)

    def status(self):
        config=dotenv_values(BACKEND / '.env')
        return {**self.store.get().get('mailbox',{}),'checking':self.lock.locked(),
            'enabled':config.get('MAIL_POLL_ENABLED','false').lower()=='true',
            'llm_configured':bool(config.get('GEMINI_API_KEY')),'model':config.get('GEMINI_MODEL','gemini-3.8-flash'),
            'fallback_enabled':config.get('GROQ_FALLBACK_ENABLED','false').lower()=='true' and bool(config.get('GROQ_API_KEY')),
            'fallback_model':config.get('GROQ_MODEL','openai/gpt-oss-120b')}

    def process(self):
        state=self.store.get()
        for thread in state['conversations']:
            for incoming in thread['messages']:
                if incoming.get('processing_status')!='pending':continue
                if incoming.get('no_reply'):continue
                decision=None
                blocked=human_block(thread, incoming)
                try:
                    if blocked:raise ValueError('Existing message requires human review.')
                    context=context_for(state,thread,incoming)
                    def save_context(snapshot):
                        t=next(c for c in snapshot['conversations'] if c['id']==thread['id'])
                        m=next(m for m in t['messages'] if m.get('id')==incoming['id'])
                        m['llm_context']=context
                        m['llm_attempt_at']=timestamp()
                    self.store.mutate(save_context)
                    decision=classify(context)
                    from .availability import validate_availability, apply_pause
                    availability = decision.intent=='out_of_office' or decision.reminder_resume_on is not None
                    if incoming.get('automated') and decision.intent!='out_of_office':raise ValueError('Automated message requires review; no automatic reply.')
                    if availability:validate_availability(decision,incoming)
                    text=None if decision.intent=='out_of_office' else render(decision,context)
                    fresh=self.store.get()
                    fresh_thread=next(c for c in fresh['conversations'] if c['id']==thread['id'])
                    fresh_in=next(m for m in fresh_thread['messages'] if m.get('id')==incoming['id'])
                    if context_for(fresh,fresh_thread,fresh_in)!=context:
                        raise ValueError('Context changed during generation; review the response.')
                    def record(s):
                        t=next(c for c in s['conversations'] if c['id']==thread['id'])
                        m=next(m for m in t['messages'] if m.get('id')==incoming['id'])
                        if availability:apply_pause(s,thread['id'],incoming['id'],decision)
                        if decision.intent=='out_of_office':m.update(processing_status='completed',message_category='out_of_office',review_kind=None)
                        m['llm_decision']=decision.model_dump()
                        m['llm_provider']=decision._provider
                        m['llm_model']=decision._model
                        m['fallback_reason']=decision._fallback_reason
                    self.store.mutate(record)
                    if text is not None:deliver(self.store,thread['id'],incoming['id'],text)
                except Exception as error:
                    reason=str(error) if isinstance(error,ValueError) else 'Agent processing failed; review required.'
                    def flag(s):
                        t=next(c for c in s['conversations'] if c['id']==thread['id'])
                        m=next(m for m in t['messages'] if m.get('id')==incoming['id'])
                        m['processing_status']='attention'
                        attempted=any(r.get('reply_to_id')==incoming['id'] for r in t['messages'])
                        m['review_kind']='delivery_failed' if attempted else 'human_review' if decision or blocked else 'generation_failed'
                        m['review_reason']=reason[:300]
                        if decision:
                            m['llm_decision']=decision.model_dump()
                            m['llm_provider']=decision._provider
                            m['llm_model']=decision._model
                            m['fallback_reason']=decision._fallback_reason
                        t.update(status='attention',reason=reason[:300],draft=decision.draft if decision else '')
                    self.store.mutate(flag)
                # Refresh between messages so an escalation stops later automatic answers.
                return self.process()

    def check(self):
        if not self.lock.acquire(blocking=False):return {'message':'Inbox check already in progress.'}
        try:
            count=fetch_new(self.store)
            self.process()
            return {'message':f'{count} new relevant emails imported. Processing complete.'}
        except ValueError as error:
            self.store.mutate(lambda s:s.setdefault('mailbox',{}).update(error=str(error)))
            raise
        finally:self.lock.release()

    def reviewed_send(self,identifier,text):
        if not self.lock.acquire(blocking=False):raise ValueError('Wait for inbox processing to finish.')
        try:
            thread=next(c for c in self.store.get()['conversations'] if c['id']==identifier)
            incoming=next(m for m in reversed(thread['messages']) if m.get('transport')=='imap')
            deliver(self.store,identifier,incoming['id'],text)
        finally:self.lock.release()

    def retry_generation(self, identifier):
        if not self.lock.acquire(blocking=False):
            raise ValueError('Wait for the current inbox check to finish.')
        try:
            def reset_failed(state):
                thread=next((c for c in state['conversations'] if c['id']==identifier),None)
                if not thread:raise ValueError('Conversation not found.')
                incoming=next((m for m in reversed(thread['messages']) if m.get('transport')=='imap'),None)
                recover_legacy(thread)
                if human_block(thread, incoming):
                    raise ValueError('An earlier message still requires human review.')
                if not incoming or not incoming.get('verified') or incoming.get('llm_decision'):
                    raise ValueError('Only failed generation can be retried; human-review decisions must be reviewed.')
                if incoming.get('processing_status')!='attention' or not (
                    incoming.get('review_kind')=='generation_failed' or thread.get('reason','').startswith('Gemini')):
                    raise ValueError('This message is not awaiting a generation retry.')
                if any(m.get('reply_to_id')==incoming.get('id') for m in thread['messages']):
                    raise ValueError('A send was already attempted; inspect delivery before retrying.')
                incoming['processing_status']='pending'
                thread.update(status='open');thread.pop('reason',None);thread.pop('draft',None)
            self.store.mutate(reset_failed)
            self.process()
            return {'message':'Generation retried. Check the conversation for delivery or review status.'}
        finally:self.lock.release()

    def start(self):
        def loop():
            while not self.stop_event.is_set():
                cfg=dotenv_values(BACKEND / '.env')
                enabled=cfg.get('MAIL_POLL_ENABLED','false').lower()=='true'
                try: interval=max(15,int(cfg.get('MAIL_POLL_INTERVAL_SECONDS','60')))
                except ValueError: interval=60
                if self.stop_event.wait(interval if enabled else 5):break
                cfg=dotenv_values(BACKEND / '.env')
                if cfg.get('MAIL_POLL_ENABLED','false').lower()!='true':continue
                try:self.check()
                except Exception:
                    self.store.mutate(lambda s:s.setdefault('mailbox',{}).update(
                        error='Automatic inbox check failed; the next scheduled check will retry.'))
        self.worker=threading.Thread(target=loop,daemon=True);self.worker.start()

    def close(self):
        self.stop_event.set()
        if self.worker:self.worker.join()
