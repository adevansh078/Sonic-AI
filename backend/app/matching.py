"""Local, explainable matching. Scores are heuristics, never payment authorization."""
import math
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

STOP = set('neft rtgs imps upi swift sepa cr dr payment part dues re ref reference pvt private ltd limited llp inc gmbh'.split())
ALIASES = {'pkg': 'packaging', 'tech': 'technologies', 'hosp': 'hospitality', 'clinic': 'clinics'}
REFERENCE = re.compile(r'(?<![a-z0-9])inv[\s/\-]*(\d+)(?![a-z0-9])', re.I)


def clean(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    text = REFERENCE.sub(' ', text)
    return ' '.join(ALIASES.get(w, w) for w in re.findall(r'[a-z]+', text) if w not in STOP)


def token_ratio(a, b):
    a, b = set(a.split()), set(b.split())
    common = a & b
    joined = lambda words: ' '.join(sorted(words))
    ratio = lambda x, y: SequenceMatcher(None, x, y).ratio() if x and y else 0
    left, right, shared = joined(a), joined(b), joined(common)
    return max(ratio(left, right), ratio(shared, left), ratio(shared, right))


def grams(text):
    text = ' ' + text + ' '
    return Counter(text[i:i+n] for n in (3, 4) for i in range(len(text)-n+1))


def name_scores(query, names):
    docs = [grams(n) for n in names]
    frequency = Counter(g for doc in docs for g in doc)
    def vector(doc):
        return {g: (1 + math.log(count)) * (1 + math.log((1+len(docs))/(1+frequency[g])))
                for g, count in doc.items()}
    q = vector(grams(query))
    norm = lambda v: math.sqrt(sum(x*x for x in v.values()))
    scores = []
    for name, doc in zip(names, docs):
        v = vector(doc)
        cosine = sum(x*v.get(g, 0) for g, x in q.items()) / max(norm(q)*norm(v), 1e-12)
        exact = bool(name and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', query))
        scores.append(1.0 if exact else .55*token_ratio(query, name)+.45*cosine)
    return scores


def match(transaction, invoices, customers=None):
    def result(invoice=None, score=0, reason='', candidates=None):
        return dict(invoice=invoice, score=score, reason=reason, candidates=candidates or [], algorithm='fuzzy-v2')
    if not transaction.get('credit'):
        return result(reason='Outgoing transaction; not a customer receipt')
    eligible = [i for i in invoices if i['status']=='sent' and i['balance_due']>0
                and i['currency']==transaction['currency'] and i['invoice_date']<=transaction['txn_date']]
    if not eligible:
        return result(reason='No unpaid invoices with compatible currency and invoice date')
    text = transaction.get('narration', '')+' '+transaction.get('reference', '')
    refs = {'INV-'+m for m in REFERENCE.findall(text)}
    known = {i['invoice_number'].upper(): i for i in invoices}
    if any(r not in known for r in refs):
        return result(reason='Unknown invoice reference; choose manually')
    referenced_pool = [i for i in eligible if i['invoice_number'].upper() in refs]
    if refs and len(referenced_pool)!=len(refs):
        return result(reason='A referenced invoice is paid, draft, or incompatible with currency/date')
    if len({i['customer_id'] for i in referenced_pool})>1:
        return result(reason='Referenced invoices belong to different customers; review manually')
    referenced = next((i for i in eligible if i['invoice_number'].upper() in refs), None)
    if refs and referenced is None:
        return result(reason='Referenced invoice is paid, draft, or incompatible with currency/date')
    # Compare all known customers, including those without eligible invoices, to detect conflicts.
    customer_names = {}
    for i in invoices:
        customer_names.setdefault(i['customer_id'], [i['customer_name']])
    for c in customers or []:
        customer_names[c['customer_id']] = [c['name']]+c.get('aliases', [])
    entries = [(cid, clean(n)) for cid, names in customer_names.items() for n in names]
    scores = name_scores(clean(transaction.get('narration', '')), [n for _, n in entries])
    payer = {}
    for (cid, _), score in zip(entries, scores):
        payer[cid] = max(payer.get(cid, 0), score)
    ranked = sorted(payer, key=payer.get, reverse=True)
    best = ranked[0] if ranked else None
    strong = best is not None and payer[best]>=.72
    clear = strong and (len(ranked)<2 or payer[best]-payer[ranked[1]]>=.12)
    if referenced and strong and (not clear or best!=referenced['customer_id']):
        return result(reason='Invoice reference conflicts with or has ambiguous payer evidence; review manually')
    if not referenced and not clear:
        return result(reason='Payer uncertain or unknown; amount alone is insufficient')
    if len(refs)>1:
        from .allocations import referenced_allocations
        options, truncated=referenced_allocations(referenced_pool,transaction['credit'])
        partial=any(any(row['remaining']>0 for row in option) for option in options)
        reason=('Allocation search limit reached; options are incomplete' if truncated else
                'No full/TDS or one-partial allocation fits all referenced invoices' if not options else
                'Review allocation across all referenced invoices; alternatives include one partial payment' if partial else
                'Multiple TDS allocations fit the referenced invoices; choose manually' if len(options)>1 else
                'All referenced invoices match one combined settlement')
        output=result(score=95 if len(options)==1 and not truncated and not partial else 0,reason=reason)
        output.update(allocations=options,search_truncated=truncated,
                      allocation_scope='All referenced invoices; at most one partial invoice in suggested options.')
        return output
    pool = [referenced] if referenced else [i for i in eligible if i['customer_id']==best]
    if not referenced:
        from .allocations import combinations
        options, truncated = combinations(pool, transaction['credit'])
        if truncated or any(len(option)>1 for option in options):
            output = result(reason='Combination search limit reached; manual review required' if truncated else
                            'Unique combined settlement; review cash and TDS before approval' if len(options)==1 else
                            'Ambiguous: multiple settlement allocations fit; choose manually',
                            score=80 if len(options)==1 and not truncated else 0)
            output['allocations'] = options
            output['search_truncated'] = truncated
            return output
    candidates = []
    money = lambda n: math.floor(float(n)*100+.5)
    for i in pool:
        amount, balance = money(transaction['credit']), money(i['balance_due'])
        kind = 'exact balance' if amount==balance else 'possible 10% TDS' if (
            i['currency']=='INR' and balance-amount==money(i['subtotal']*.1)) else 'partial payment' if amount<balance else 'overpayment'
        if kind=='overpayment':
            continue
        score = min(99, (70 if referenced else round(55*payer[best])) +
                    {'exact balance':25, 'possible 10% TDS':20, 'partial payment':5}[kind])
        reason = ('Invoice reference found' if referenced else 'Fuzzy payer match')+'; '+kind
        candidates.append(dict(invoice=i['invoice_number'], score=score, reason=reason,
                               customer_name=i['customer_name'], balance_due=i['balance_due'], currency=i['currency']))
    candidates.sort(key=lambda c: (-c['score'], c['invoice']))
    if not candidates:
        return result(reason='No compatible amount for the identified payer/reference; review manually')
    top = candidates[0]
    if len(candidates)>1 and top['score']-candidates[1]['score']<10:
        return result(score=top['score'], reason='Ambiguous: multiple invoices have similar evidence; choose manually', candidates=candidates)
    return result(top['invoice'], top['score'], top['reason'], candidates)
