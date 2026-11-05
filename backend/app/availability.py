"""Conservative reminder pauses; never modifies contractual invoice dates."""
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from .logic import timestamp

ZONE = ZoneInfo('Asia/Kolkata')
AWAY = re.compile(r'out[ -]of[ -](?:the[ -])?office|on (?:leave|vacation|holiday)|\baway\b', re.I)
EXTENSION = re.compile(r'extend|extension|more time|postpone|defer|instal+ment|installment|pay later|payment deadline|due date', re.I)

def latest_text(text):
    return re.split(r'(?im)^On .+wrote:|^-{2,}\s*(?:Original|Forwarded) message',text)[0].split('\n>')[0]

def pause_active(customer):
    resume=customer.get('reminder_pause',{}).get('resume_on')
    return bool(resume and resume>datetime.now(ZONE).date().isoformat())

def validate_availability(decision, incoming):
    text=latest_text(incoming['text'])
    if EXTENSION.search(text):
        raise ValueError('Payment timing or extension request requires human review; no reminder pause applied.')
    if not incoming.get('verified') or decision.requires_human_review or decision.confidence<.9:
        raise ValueError('Availability needs human review; no reminder pause applied.')
    evidence=decision.availability_evidence.strip()
    if not evidence or evidence not in text or not AWAY.search(text):
        raise ValueError('Availability evidence is missing or ambiguous; review the email.')
    received=datetime.fromisoformat(incoming.get('at',timestamp()).replace('Z','+00:00')).astimezone(ZONE).date()
    values=re.findall(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s*[A-Za-z]+(?:\s+\d{4})?\b',evidence)
    if len(values)!=1:raise ValueError('Missing or multiple availability dates require review.')
    value=re.sub(r'^(\d{1,2})\s*([A-Za-z]+)',r'\1 \2',values[0]);parsed=None
    for fmt in ('%Y-%m-%d','%d %B %Y','%d %b %Y'):
        try:parsed=datetime.strptime(value,fmt).date();break
        except ValueError:pass
    if parsed is None:
        for fmt in ('%d %B %Y','%d %b %Y'):
            try:parsed=datetime.strptime(value+' '+str(received.year),fmt).date();break
            except ValueError:pass
        if parsed is not None and parsed<received:
            try:parsed=parsed.replace(year=parsed.year+1)
            except ValueError:parsed=None
    if parsed is None:raise ValueError('Unrecognized availability date; human review required.')
    if re.search(r'\b(?:through|till|until)\b',evidence,re.I):resume=parsed+timedelta(days=1)
    elif re.search(r'\b(?:back|return|returning|resume)\b',evidence,re.I):resume=parsed
    else:raise ValueError('Return-day meaning is unclear; confirm when reminders may resume.')
    today=datetime.now(ZONE).date()
    if resume<=today or resume>today+timedelta(days=90):
        raise ValueError('Availability date is past or beyond 90 days; human review required.')
    if decision.reminder_resume_on!=resume.isoformat():
        raise ValueError('Model date does not match the email evidence; human review required.')
    return resume.isoformat()

def apply_pause(state, thread_id, incoming_id, decision):
    thread=next(t for t in state['conversations'] if t['id']==thread_id)
    incoming=next(m for m in thread['messages'] if m.get('id')==incoming_id)
    resume=validate_availability(decision,incoming)
    customer=next(c for c in state['customers'] if c['customer_id']==thread['customer_id'])
    previous=customer.get('reminder_pause',{})
    if previous.get('resume_on','')>resume and pause_active(customer):
        raise ValueError('A longer reminder pause exists; review before shortening it.')
    customer['reminder_pause']=dict(resume_on=resume,timezone='Asia/Kolkata',source_email_id=incoming_id,
        reason=decision.availability_evidence,recorded_at=timestamp())
