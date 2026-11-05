"""Delivery notifications never enter the customer-response pipeline."""
import re
from email.utils import parseaddr
from uuid import uuid4
from .logic import timestamp


def ingest_report(state, parsed, identity):
    structured=parsed.get_content_type()=='multipart/report' and parsed.get_param('report-type')=='delivery-status'
    sender=parseaddr(str(parsed.get('From','')))[1].lower()
    possible=bool(re.search(r'mailer-daemon|postmaster',sender,re.I) and
                  re.search(r'undeliver|delivery|failure|returned mail',str(parsed.get('Subject','')),re.I))
    if not structured and not possible:return False
    mid=str(parsed.get('Message-ID',''))
    if mid and any(m.get('message_id')==mid and m.get('sender')==sender for t in state['conversations'] for m in t['messages']):return True
    ids=set(); outcomes=[]
    for part in parsed.walk():
        if part.get_content_type()=='message/delivery-status':
            for block in part.get_payload():
                ids.update(re.findall(r'<[^<>]+>',str(block.get('Original-Message-ID',''))))
                recipient=str(block.get('Final-Recipient',block.get('Original-Recipient',''))).split(';')[-1].strip().lower()
                if recipient:outcomes.append(dict(recipient=recipient,action=str(block.get('Action','')).lower(),
                    status=str(block.get('Status','')),diagnostic=str(block.get('Diagnostic-Code',''))[:500]))
        elif part.get_content_type()=='message/rfc822':
            for original in part.get_payload():ids.update(re.findall(r'<[^<>]+>',str(original.get('Message-ID',''))))
        elif part.get_content_type()=='text/rfc822-headers':
            payload=part.get_payload(decode=True) or b''
            ids.update(re.findall(r'(?im)^Message-ID:\s*(<[^<>]+>)',payload.decode('utf-8','replace')))
    ids.update(re.findall(r'<[^<>]+>',str(parsed.get('In-Reply-To',''))+' '+str(parsed.get('References',''))))
    auth=str((parsed.get_all('Authentication-Results') or [''])[0])
    domain=sender.rsplit('@',1)[-1]
    authenticated=bool(re.match(r'\s*mx\.google\.com;',auth) and 'dmarc=pass' in auth.lower()
                       and re.search(r'header\.from='+re.escape(domain)+r'(?:[;\s]|$)',auth,re.I))
    for outcome in outcomes or [dict(recipient='',action='',status='',diagnostic='Nonstandard or incomplete delivery report')]:
        matches=[(t,m) for t in state['conversations'] for m in t['messages'] if m.get('transport')=='smtp'
                 and m.get('message_id') in ids and m.get('to','').lower()==outcome['recipient']]
        linked=len(matches)==1
        confirmed=structured and linked and authenticated
        action=outcome['action']; code=outcome['status']
        kind='delivery_failed' if confirmed and action=='failed' and code.startswith('5.') else (
             'delivery_delayed' if confirmed and action=='delayed' and code.startswith('4.') else (
             'delivery_success' if confirmed and action in ('delivered','relayed','expanded') and code.startswith('2.') else 'delivery_unverified'))
        thread=matches[0][0] if linked else dict(id=str(uuid4()),customer_id=None,invoice='Delivery report',status='open',messages=[])
        if not linked:state['conversations'].append(thread)
        summary=f"{kind.replace('_',' ').title()}: {outcome['recipient'] or 'recipient unknown'}. {code} {outcome['diagnostic']}"
        attention=kind in ('delivery_failed','delivery_unverified')
        thread['messages'].append(dict(id=str(uuid4()),direction='in',transport='imap',message_category=kind,
            text=summary,at=timestamp(),sender=sender,message_id=mid,mailbox_identity=identity,
            processing_status='attention' if attention else 'completed',review_kind='delivery_report' if attention else None,
            review_reason=summary if attention else None,verified=False,automated=True,no_reply=True,
            original_message_id=matches[0][1]['message_id'] if linked else None,delivery_report=outcome))
        if attention:thread.update(status='attention',reason=summary,draft='')
        if confirmed:
            outgoing=matches[0][1]
            outgoing.setdefault('delivery_events',[]).append(dict(category=kind,at=timestamp(),**outcome))
            outgoing['delivery_notice']=kind
    return True
