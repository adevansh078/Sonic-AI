"""Bounded IMAP retrieval. Save before processing; never mark messages read."""
import email
import imaplib
import re
import ssl
from email import policy
from email.utils import parseaddr
from datetime import datetime, timezone
from uuid import uuid4
from dotenv import dotenv_values
from .store import BACKEND
from .logic import timestamp
from .review import recover_legacy, human_block


def ingest(state, raw, uid, validity, account):
    box = state.setdefault('mailbox', {})
    identity = f'{account}:INBOX:{validity}:{uid}'
    seen = box.setdefault('seen', [])
    if identity in seen:
        return False
    parsed = email.message_from_bytes(raw, policy=policy.default)
    from .bounces import ingest_report
    if ingest_report(state,parsed,identity):
        seen.append(identity)
        return True
    sender = parseaddr(str(parsed.get('From', '')))[1].lower()
    mid = str(parsed.get('Message-ID', ''))
    all_messages = [m for c in state['conversations'] for m in c['messages']]
    if mid and any(m.get('message_id') == mid and m.get('sender') == sender for m in all_messages):
        seen.append(identity)
        return False
    refs = re.findall(r'<[^<>]+>', str(parsed.get('In-Reply-To', '')) + ' ' + str(parsed.get('References', '')))
    matched = [c for c in state['conversations'] if any(m.get('message_id') in refs for m in c['messages'])]
    customers = [c for c in state['customers'] if c['contact_email'].lower() == sender]
    # Do not ingest unrelated personal mail; only known customers or replies to app threads.
    if not matched and not customers:
        seen.append(identity)
        return False
    thread = matched[0] if len(matched) == 1 else None
    verified = (len(customers) == 1 and (thread is None or thread['customer_id'] == customers[0]['customer_id']))
    # Gmail's own Authentication-Results is additional evidence, not a user-authored body claim.
    auth = parsed.get_all('Authentication-Results', [])
    authenticated = bool(auth and re.match(r'\s*mx\.google\.com;', str(auth[0])) and
                         'dmarc=pass' in str(auth[0]).lower() and
                         re.search(r'header\.from=' + re.escape(sender.split('@')[-1]) + r'(?:[;\s]|$)', str(auth[0]), re.I))
    if thread is None:
        thread = dict(id=str(uuid4()), customer_id=customers[0]['customer_id'] if len(customers)==1 else None,
                      invoice='Email inquiry', status='open', messages=[])
        state['conversations'].append(thread)
    part = parsed.get_body(preferencelist=('plain',))
    body = part.get_content() if part else '[No plain-text body; review email in Gmail.]'
    automated = str(parsed.get('Auto-Submitted', 'no')).lower() != 'no' or parsed.get_content_type() == 'multipart/report'
    reason = None
    if not verified or not authenticated or len(matched)>1:
        reason = 'Sender/thread identity requires review; no automatic disclosure.'
    elif (automated and not (str(parsed.get('Auto-Submitted','')).lower()=='auto-replied' and re.search(r'out.of.office|on leave|on vacation', body, re.I) and parsed.get_content_type()!='multipart/report')) or part is None or len(body)>12000:
        reason = 'Automated, HTML-only, or oversized email requires review.'
    recover_legacy(thread)
    if human_block(thread):
        reason = reason or 'Conversation already awaits human review.'
    thread['messages'].append(dict(id=str(uuid4()), direction='in', transport='imap',
        text=body[:12000], subject=str(parsed.get('Subject', ''))[:300], sender=sender,
        message_id=mid, references=refs, mailbox_identity=identity, at=timestamp(),
        processing_status='attention' if reason else 'pending', verified=verified and authenticated,
        review_kind='human_review' if reason else None, review_reason=reason, automated=automated))
    if reason:
        thread.update(status='attention', reason=reason, draft='')
    seen.append(identity)
    return True


def fetch_new(store):
    config = dotenv_values(BACKEND / '.env')
    account = (config.get('MAIL_USERNAME') or '').strip().lower()
    password = ''.join((config.get('MAIL_APP_PASSWORD') or '').split())
    if not account or len(password)!=16:
        raise ValueError('Configure the company Gmail address and App Password first.')
    box = store.get().get('mailbox', {})
    starts = [m['at'] for c in store.get()['conversations'] for m in c['messages']
              if m.get('transport')=='smtp' and m.get('delivery_status')=='accepted']
    since = box.get('since') or (min(starts)[:10] if starts else datetime.now(timezone.utc).date().isoformat())
    count = 0
    try:
        with imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=25) as client:
            client.login(account, password)
            status, _ = client.select('INBOX', readonly=True)
            if status != 'OK': raise ValueError('Cannot open INBOX.')
            validity = client.response('UIDVALIDITY')[1][0].decode()
            cursor = box.get('last_uid', 0) if box.get('validity')==validity and box.get('account')==account else 0
            date_text = datetime.fromisoformat(since).strftime('%d-%b-%Y')
            status, result = client.uid('search', None, f'(UID {cursor+1}:* SINCE {date_text})')
            if status!='OK': raise ValueError('IMAP search failed.')
            uids = sorted(int(x) for x in result[0].split() if int(x)>cursor)[:50]
            for uid in uids:
                status, result = client.uid('fetch', str(uid), '(RFC822.SIZE)')
                sizes = re.findall(rb'RFC822.SIZE (\d+)', b' '.join(x for x in result if isinstance(x,bytes)))
                if status!='OK' or not sizes: raise ValueError('IMAP size lookup failed.')
                oversized = int(sizes[0])>1024*1024
                status, result = client.uid('fetch', str(uid), '(BODY.PEEK[HEADER])' if oversized else '(BODY.PEEK[])')
                if status!='OK': raise ValueError('IMAP message retrieval failed.')
                raw = next((x[1] for x in result if isinstance(x,tuple)), None)
                if raw is None: raise ValueError('IMAP returned no message body.')
                def save(state):
                    added = ingest(state, raw, uid, validity, account)
                    state.setdefault('mailbox', {}).update(account=account, validity=validity,last_uid=uid,since=since)
                    return added
                count += int(store.mutate(save))
            store.mutate(lambda s:s.setdefault('mailbox',{}).update(last_checked=timestamp(),error=None,since=since,account=account))
        return count
    except Exception as error:
        raise ValueError(f'Inbox check failed ({type(error).__name__}); verify Gmail access and connection.') from None
