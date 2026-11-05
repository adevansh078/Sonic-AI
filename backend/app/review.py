"""Per-message review provenance; technical failures must not lock a thread."""

def recover_legacy(thread):
    # Old versions lost the technical error when a subsequent reply arrived.
    reason = thread.get('reason', '')
    if reason != 'Conversation already awaits human review.' and not reason.startswith('Gemini'):
        return
    pending = [m for m in thread['messages'] if m.get('transport') == 'imap'
               and m.get('processing_status') == 'attention']
    if not pending or thread.get('draft') or any(
        not m.get('verified') or m.get('llm_decision') or m.get('review_kind')
        or any(r.get('reply_to_id') == m.get('id') for r in thread['messages'])
        for m in pending):
        return
    for m in pending:
        m.update(review_kind='generation_failed', review_reason=
                 'Legacy generation failure or inherited conversation block; retry AI processing.')
    thread['reason'] = pending[-1]['review_reason']


def human_block(thread, incoming=None):
    return any(m is not incoming and m.get('processing_status') == 'attention'
               and m.get('review_kind') != 'generation_failed'
               for m in thread['messages'])
