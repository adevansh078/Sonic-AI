import React, {useState} from 'react';

export default function ReminderComposer({customers, invoices, api, run, busy}) {
  const [open, setOpen] = useState(false);
  const [customer, setCustomer] = useState('');
  const [selected,setSelected]=useState([]);
  const customerInvoices=invoices.filter(i=>i.customer_id===customer&&i.status==='sent'&&i.balance_due>0);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const eligible = customers.filter(c => invoices.some(i => i.customer_id === c.customer_id && i.status === 'sent' && i.balance_due > 0));

  async function prepare() {
    setLoading(true); setError(''); setPreview(null);
    try {setPreview(await api(`/customers/${customer}/reminder-preview?${new URLSearchParams(selected.map(id=>['invoice_numbers',id]))}`, null, 'GET'));}
    catch (e) {setError(e.message);}
    finally {setLoading(false);}
  }
  async function send() {
    if (await run(() => api(`/customers/${customer}/reminder`, {preview_id: preview.preview_id, invoice_numbers: preview.invoice_numbers}))) {
      setOpen(false); setPreview(null);
    } else {
      setError('Sending did not complete successfully. Check the page notice and conversation delivery status before retrying.');
      setPreview(null);
    }
  }
  return <>
    <button className="primary" disabled={busy} onClick={() => {setOpen(true); setPreview(null); setError('');}}>Prepare reminder</button>
    {open && <div className="reminder-overlay"><section className="reminder-dialog" role="dialog" aria-modal="true" aria-labelledby="reminder-title">
      <h2 id="reminder-title">Send one customer reminder</h2>
      <p>Choose one or more sent, unpaid invoices to include in one email. Invoices not yet due are marked separately.</p>
      <label htmlFor="reminder-customer">Customer</label>
      <select id="reminder-customer" disabled={loading || busy} value={customer} onChange={e => {setCustomer(e.target.value); setSelected(invoices.filter(i=>i.customer_id===e.target.value&&i.status==='sent'&&i.balance_due>0).map(i=>i.invoice_number)); setPreview(null); setError('');}}>
        <option value="">Select a customer</option>
        {eligible.map(c => <option key={c.customer_id} value={c.customer_id} disabled={c.reminders_paused}>{c.name} — {c.reminders_paused?'Out of office; resumes '+c.reminder_pause.resume_on:c.contact_email}</option>)}
      </select>
      {customer&&<fieldset disabled={loading||busy} style={{border:'1px solid #ccd7de',borderRadius:8,padding:14,marginBottom:16}}>
        <legend>Invoices to include ({selected.length} selected)</legend>
        <button type="button" onClick={()=>{setSelected(customerInvoices.map(i=>i.invoice_number));setPreview(null);}}>Select all</button>{' '}
        <button type="button" onClick={()=>{setSelected([]);setPreview(null);}}>Clear selection</button>
        {customerInvoices.map(i=><label key={i.invoice_number} style={{display:'flex',gap:10,alignItems:'center',marginTop:12}}>
          <input type="checkbox" checked={selected.includes(i.invoice_number)} onChange={e=>{setSelected(previous=>e.target.checked?[...previous,i.invoice_number]:previous.filter(id=>id!==i.invoice_number));setPreview(null);setError('');}}/>
          <span><b>{i.invoice_number}</b> · {i.currency} {i.balance_due.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})} · due {i.due_date}</span>
        </label>)}
        {!selected.length&&<p>Select at least one invoice to preview.</p>}
      </fieldset>}
      {!eligible.length && <p>No customers have outstanding invoices.</p>}
      {error && <p role="alert">{error}</p>}
      {preview && <div className="reminder-preview">
        <p><b>From:</b> {preview.sender}<br/><b>To:</b> {preview.recipient}<br/><b>Subject:</b> {preview.subject}</p>
        <pre>{preview.body}</pre>
        {preview.blocked_reason && <p role="alert">{preview.blocked_reason}</p>}
      </div>}
      <div className="reminder-actions">
        <button disabled={busy || loading} onClick={() => setOpen(false)}>Cancel</button>
        {!preview ? <button className="primary" disabled={!customer || !selected.length || busy || loading} onClick={prepare}>{loading ? 'Preparing…' : 'Preview email'}</button>
          : <button className="primary" disabled={busy || !!preview.blocked_reason} onClick={send}>{busy ? 'Sending…' : 'Send reminder'}</button>}
      </div>
    </section></div>}
  </>;
}
