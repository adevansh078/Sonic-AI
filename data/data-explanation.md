# Data explanation

This folder holds the test data for the assignment. Everything in it is fictional: the companies, people, bank accounts and transactions. Use the files as given, except for the email addresses described below.

## The story behind the data

AcmeCorp is the organization your application works for. It sells software and services to nine customers. It sends them invoices, reminds them to pay, and receives their payments in its bank account.

The bank statement lists the money that came in and went out. It says nothing about which invoice a payment was for, so your application has to work that out and suggest it to a person.

| File | What it holds | Rows |
|---|---|---|
| `organization.json` | AcmeCorp's contacts, bank details and billing policies | 1 organization |
| `customers.json` | The companies AcmeCorp bills | 9 customers |
| `invoices.json` | The invoices AcmeCorp has created | 27 invoices |
| `bank_statement_sample.csv` | A sample bank statement to build with | 14 rows |

The files link together like this:

- An invoice belongs to one customer, through `customer_id`.
- A statement row may pay one or more invoices, or none. There is no link in the data. Finding it is your task.

## organization.json

| Field | Meaning |
|---|---|
| `name`, `address`, `phone` | AcmeCorp's details |
| `accounts_contact_name`, `accounts_email` | Who customers write to. Replace `accounts_email` with your own organization test inbox |
| `default_currency`, `accepted_currencies` | AcmeCorp reports in INR and accepts INR, USD, EUR and GBP |
| `payment_terms`, `payment_methods` | How customers should pay |
| `bank_details` | The account customers pay into |
| `policies` | Rules on TDS, bank charges, extensions, disputes and working hours. The AI should use these when it answers customers |

## customers.json

| Field | Meaning | Example |
|---|---|---|
| `customer_id` | Unique ID, used by invoices | `C002` |
| `name` | Registered company name | `Kestrel Foods Private Limited` |
| `contact_name` | The person AcmeCorp deals with | `Arjun Nair` |
| `contact_email` | Where emails go. These use `example.com` and cannot receive mail, so replace them with your own test inboxes while you build | `customer2@example.com` |
| `phone`, `city`, `country` | Contact details and location | `Kochi`, `India` |
| `currency` | The currency this customer is billed in | `INR` |

## invoices.json

| Field | Meaning | Example |
|---|---|---|
| `invoice_number` | Unique number | `INV-1004` |
| `customer_id`, `customer_name` | Who is billed | `C002` |
| `po_number` | The customer's purchase order, if there is one. It can be `null` | `BL-4520` |
| `invoice_date` | The date of the invoice | `2026-08-20` |
| `due_date` | When payment is due: the invoice date plus the payment terms | `2026-09-19` |
| `payment_terms` | Days allowed for payment | `Net 30` |
| `currency` | `INR`, `USD`, `EUR` or `GBP` | `INR` |
| `line_items` | What was sold: description, quantity, unit price and amount | |
| `subtotal` | The sum of the line items, before discount and tax | `50000.0` |
| `discount` | Amount taken off the subtotal | `0.0` |
| `tax_name` | `GST 18%` for customers in India. `No tax (export)` for customers abroad | `GST 18%` |
| `tax` | Tax amount | `9000.0` |
| `total` | Subtotal minus discount, plus tax | `59000.0` |
| `amount_paid` | What has already been received | `0.0` |
| `balance_due` | Total minus amount paid. This is what the customer still owes | `59000.0` |
| `status` | `draft`, `sent` or `paid` | `sent` |

Amounts are in major units, such as rupees or dollars, not paise or cents. Dates are `YYYY-MM-DD`.

### Invoice status

| Status | Meaning | Count |
|---|---|---|
| `draft` | Not sent to the customer yet, so the customer does not owe it | 2 |
| `sent` | Sent to the customer. Some have money still unpaid | 21 |
| `paid` | Fully paid | 4 |

"Overdue" is not stored. An invoice is overdue when it is `sent`, has a balance due, and its due date has passed. Work this out from today's date.

### Invoices by currency

| Currency | Customers | Invoices |
|---|---|---|
| INR | 6 | 20 |
| USD | 1 | 3 |
| EUR | 1 | 2 |
| GBP | 1 | 2 |

## bank_statement_sample.csv

| Column | Meaning | Example |
|---|---|---|
| `txn_date` | The date the money moved | `2026-09-05` |
| `narration` | The bank's free-text description. It is short and inconsistent, and it may name the payer or an invoice | `IMPS/KESTREL FOODS/INV 1004` |
| `reference` | The bank's reference for the transaction. It can be blank | `624811204417` |
| `credit` | Money received, in the row's currency. Blank if money went out | `59000` |
| `debit` | Money paid out, in the row's currency. Blank if money came in | `185000` |
| `currency` | The currency of the row | `INR` |

Transfer methods appear at the start of many narrations. NEFT, RTGS, IMPS and UPI are Indian transfers, SWIFT is an international transfer, and SEPA is a European transfer. `CR` means money in, and `DR` means money out.

We will test with a different statement in the same format, against the same customers and invoices.
