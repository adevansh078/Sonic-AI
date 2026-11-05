"""Derive the review queue from unresolved work, not historical sent messages."""
def prepare_conversation(thread):
    messages = thread['messages']
    failed = [m for m in messages if m.get('transport') == 'smtp'
              and m.get('delivery_status') in ('failed', 'unknown')
              and not (m.get('reply_to_id') and any(
                  r.get('reply_to_id') == m['reply_to_id'] and r.get('delivery_status') == 'accepted'
                  for r in messages))]
    if failed:
        thread['status'] = 'attention'
        thread['reason'] = 'Email delivery failed or is uncertain; inspect delivery before retrying.'
        return thread
    incoming = [m for m in messages if m.get('transport') == 'imap']
    if incoming:
        unresolved = [m for m in incoming if m.get('processing_status') == 'attention' and not any(
            reply.get('reply_to_id') == m.get('id') and reply.get('delivery_status') == 'accepted'
            for reply in messages)]
        if unresolved:
            thread['status'] = 'attention'
            if 'Gemini' in thread.get('reason', '') and not unresolved[-1].get('llm_decision'):
                thread['draft'] = ''
        elif all(m.get('processing_status') == 'completed' or any(
                reply.get('reply_to_id') == m.get('id') and reply.get('delivery_status') == 'accepted'
                for reply in messages) for m in incoming):
            thread['status'] = 'open'
            thread.pop('reason', None)
            thread.pop('draft', None)
        else:
            thread['status'] = 'open'
            thread.pop('reason', None)
            thread.pop('draft', None)
    elif messages and all(m.get('transport') == 'smtp' for m in messages):
        # An accepted reminder alone is not an unresolved review item.
        thread['status'] = 'attention' if messages[-1].get('delivery_status') in ('failed', 'unknown') else 'open'
    return thread
