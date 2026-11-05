import unittest
from copy import deepcopy
from app.store import initial_state
from app.logic import suggest
from app.allocations import combinations, approve_allocation

class AllocationTests(unittest.TestCase):
 def setUp(self):
  self.s=initial_state()
  self.t=dict(id='receipt',status='pending',narration='SAFFRON PEAK HOSP OCT DUES',reference='',credit=194700,currency='INR',txn_date='2026-10-12')
  self.s['transactions']=[self.t]
 def test_saffron_atomic_approval_and_duplicate(self):
  r=suggest(self.t,self.s['invoices'],self.s['customers'])
  self.assertEqual(len(r['allocations']),1)
  rows=r['allocations'][0]
  self.assertEqual({x['invoice'] for x in rows},{'INV-1010','INV-1011','INV-1012'})
  self.assertEqual(sum(x['cash'] for x in rows),194700)
  approve_allocation(self.s,'receipt',rows)
  self.assertEqual(len(self.s['payments']),3)
  self.assertTrue(all(i['status']=='paid' for i in self.s['invoices'] if i['invoice_number'] in {r['invoice'] for r in rows}))
  with self.assertRaises(ValueError):approve_allocation(self.s,'receipt',rows)
 def test_stale_allocation_changes_nothing(self):
  rows=suggest(self.t,self.s['invoices'])['allocations'][0]
  next(i for i in self.s['invoices'] if i['invoice_number']=='INV-1010')['balance_due']=100
  before=deepcopy(self.s)
  with self.assertRaises(ValueError):approve_allocation(self.s,'receipt',rows)
  self.assertEqual(before,self.s)
 def test_combined_tds(self):
  self.t['credit']=194700-7000-3500-6500
  options=suggest(self.t,self.s['invoices'])['allocations']
  rows=next(r for r in options if len(r)==3 and all(x['tds'] for x in r))
  approve_allocation(self.s,'receipt',rows)
  self.assertEqual(sum(p['tds'] for p in self.s['payments']),17000)
 def test_ambiguous_and_bound(self):
  invoices=[dict(invoice_number=str(n),balance_due=v,subtotal=v,currency='USD') for n,v in enumerate([10,20,30,40])]
  options,truncated=combinations(invoices,50)
  self.assertEqual(len(options),2)
  self.assertFalse(truncated)
  self.assertTrue(combinations(invoices,50,limit=1)[1])
  self.assertTrue(combinations(invoices*5,50)[1])

 def test_explicit_references_full_and_tds(self):
  self.t['narration']='SAFFRON PEAK HOSP INV1010 INV/1011 INV-1012'
  for cash in (194700,177700):
   self.t['credit']=cash
   result=suggest(self.t,self.s['invoices'])
   self.assertTrue(result['allocations'])
   for rows in result['allocations']:
    self.assertEqual({r['invoice'] for r in rows},{'INV-1010','INV-1011','INV-1012'})
    self.assertEqual(sum(round(r['cash']*100) for r in rows),cash*100)
 def test_explicit_partial_preserves_balance(self):
  self.t.update(narration='SAFFRON PEAK HOSP INV1010 INV1011 INV1012',credit=150000)
  result=suggest(self.t,self.s['invoices'])
  self.assertGreater(len(result['allocations']),1)
  rows=next(rows for rows in result['allocations'] if all(r['tds']==0 for r in rows) and next(r for r in rows if r['invoice']=='INV-1012')['remaining']>0)
  approve_allocation(self.s,'receipt',rows)
  invoice=next(i for i in self.s['invoices'] if i['invoice_number']=='INV-1012')
  self.assertEqual(invoice['balance_due'],44700)
  self.assertEqual(invoice['status'],'sent')
  self.assertEqual(sum(p['cash'] for p in self.s['payments']),150000)
 def test_mixed_customer_or_ineligible_reference_rejected(self):
  for narration in ('SAFFRON INV1010 INV1004','SAFFRON INV1010 INV1019','SAFFRON INV1010 INV9999'):
   self.t['narration']=narration
   self.assertFalse(suggest(self.t,self.s['invoices']).get('allocations'))
