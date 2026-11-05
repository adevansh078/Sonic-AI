# Acme Collections

A local collections and bank-reconciliation application for the Sonic AI intern assignment. React displays the workspace; FastAPI handles email, model decisions, invoice matching and approvals. Data persists in TinyDB by default, with optional MongoDB support.

The supplied data is fictional: nine customers and 27 invoices across INR, USD, EUR and GBP. Real emails can be sent to your configured test inbox. A customer claiming payment never changes an invoice balance; a person must approve a bank receipt first.

## Project overview

The application connects two workflows through the same invoice records: **collections** asks customers to pay, while **reconciliation** confirms receipts after human approval. Overdue is calculated from due date, sent status and positive balance; it is not an extra stored invoice status.

| Area | Implemented behaviour |
|---|---|
| Workspace | Organization details, editable customer email addresses, invoices and outstanding/overdue balances |
| Reminders | Select one or several invoices, preview one consolidated email, then send with current amounts |
| Collections agent | Manual and automatic IMAP checks, customer/thread association, Gemini responses with optional Groq fallback |
| Human review | Separate Needs attention queue for decisions, generation failures and delivery issues; edit/write and send a reviewed reply |
| Mail exceptions | Out-of-office reminder pauses and bounce detection; delivery reports receive no AI reply |
| Reconciliation | CSV import, local fuzzy/reference/amount matching, combined allocations, partial payments and TDS review |
| Persistence | TinyDB by default or optional MongoDB; approved receipts immediately affect future reminders and agent context |

### Architecture and stack

```text
React 19 / Vite 6 frontend
        | HTTP JSON requests (/api, proxied by Vite)
Python / FastAPI API routes
        | business logic: collections agent + reconciliation + approvals
        +-- store.py --------> TinyDB JSON / optional MongoDB (PyMongo)
        +-- mail adapters ---> Gmail SMTP + IMAP
        +-- llm.py / HTTPX ---> Gemini / optional Groq fallback
```

Uvicorn serves the Python API. The frontend never contacts the database, mail server or model providers directly. Backend modules separate responsibilities within one process; they are not independent microservices. Node.js runs the frontend tooling and development launcher, not the backend business logic.

The three-page [implementation explanation](explanation.pdf) contains the architecture diagram, database design, features, algorithms and scoring. Use this README for installation and operation.

## 1. Prerequisites

Install:

- Git.
- Node.js 22+ and npm.
- Python **3.12 recommended**, with `venv` and pip; the pinned environment was verified on Python 3.12. Other Python versions have not been verified for this dependency snapshot.
- Docker with Compose only if using the optional MongoDB setup.

Confirm the tools are available with `git --version`, `node --version` and `npm --version`; on Windows check `py -3.12 --version`, or on macOS/Linux check `python3.12 --version`.

For real email testing, prepare two separate inboxes: a company Gmail account and a payer test inbox you control. The company account must support Gmail App Password authentication. Obtain a Gemini API key; a Groq key is optional for fallback.

You can explore invoices, customers and reconciliation without email or model credentials. Live collection replies need those credentials and network access.

## 2. Clone and locate the project

Replace `YOUR_GITHUB_USERNAME/YOUR_REPOSITORY` with the actual published repository path. This is a placeholder, not a live repository URL.

```sh
git clone https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
```

Run the following setup commands from the **application root**: the directory containing `package.json`, `package-lock.json`, `backend/`, `frontend/` and `data/`.

If the repository preserves the assignment's outer folder, enter the application subdirectory first:

```sh
cd acme-collections
```

If those application files are already at the repository root, skip that last command.

## 3. Install dependencies

### Windows PowerShell

```powershell
py -3.12 -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
npm ci
if (!(Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
```

If `py` is unavailable, use your installed `python` command or its full executable path for the first command. If `python` opens the Microsoft Store, use an actual installed Python executable. Virtual-environment activation is not required.

### macOS / Linux

```sh
python3.12 -m venv backend/.venv
./backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt
npm ci
if [ ! -f backend/.env ]; then cp backend/.env.example backend/.env; fi
```

If your Python 3.12 installation is exposed as `python3`, use that command after checking `python3 --version`. On Linux, install your distribution's Python venv package if venv creation reports it missing.

Copy `.env.example` only on first setup. Do not overwrite an existing `.env` containing your settings. JavaScript packages are installed at the application root using npm workspaces; do not run a separate frontend dependency installation.

Use `requirements.lock.txt` for the tested Python package versions; `requirements.txt` records the supported direct dependency ranges for deliberate upgrades. Commit both Python dependency files and `package-lock.json`. Do not copy a virtual environment or `node_modules` between devices.

If an existing Python environment reports `No module named pip`, run its Python executable with `-m ensurepip --upgrade`, then repeat the installation command. A normal fresh `python -m venv` includes pip.

## 4. Configure the backend

Edit `backend/.env` in your editor. Keep passwords and keys only in that file; never put them in frontend code or commit them. The repository's `.gitignore` excludes `.env`, runtime storage, debug files and virtual environments.

### Choose a first-run mode

| Mode | Required configuration | What to try |
|---|---|---|
| Local data/reconciliation | Keep the example configuration, blank credentials and `MONGODB_URI=`, polling false | Start the app, inspect seed data and approve sample bank receipts |
| Real reminders | Company Gmail credentials and `TEST_PAYER_EMAIL`; update one customer email | Preview and send a reminder to your test inbox |
| Automatic customer replies | Real-mail settings plus a valid Gemini key/model; optional Groq fallback | Reply from the payer inbox, then check manually or enable polling |

Start with local data mode to confirm installation before configuring live services. Dependencies still require internet access during installation; local reconciliation does not call a model. Do not leave placeholder key/password strings in settings you intend to use. Restart the backend after editing `.env`. Existing shell environment variables can take precedence over `.env`; remove stale overrides if the app appears to ignore a setting.

### Company Gmail and payer inbox

1. Enable 2-Step Verification on the company Google account.
2. Create an App Password if the account allows it. Use the 16-character App Password, not your usual Google password. Some managed/restricted accounts do not offer App Passwords; use an eligible test account. See [Google's App Password instructions](https://support.google.com/accounts/answer/185833).
3. Set the following values using your own accounts:

```dotenv
MAIL_USERNAME=your-company-test@gmail.com
MAIL_APP_PASSWORD=your-16-character-app-password
TEST_PAYER_EMAIL=your-payer-test@gmail.com
MAIL_POLL_ENABLED=false
MAIL_POLL_INTERVAL_SECONDS=60
```

The password text above is a placeholder and must be replaced. The company and payer addresses must differ. Only the company mailbox requires an App Password in this application; sign into the payer inbox normally to write replies.

The code currently uses Gmail SMTP at `smtp.gmail.com:587` with STARTTLS and IMAP at `imap.gmail.com:993` with TLS. Arbitrary mail providers and OAuth are not implemented. Your network/account policy must allow those connections.

**Important:** sending is restricted to `TEST_PAYER_EMAIL`. After startup, edit **one customer's contact email** in the Customers page to exactly match that address. Do not give several customers the same test address: sender identification would be ambiguous. Use one customer at a time for the two-inbox demo.

### Gemini and optional Groq fallback

Create a Gemini key through [Google AI Studio](https://aistudio.google.com/) and follow the [Gemini setup documentation](https://ai.google.dev/gemini-api/docs/get-started). For fallback, follow the [Groq quickstart](https://console.groq.com/docs/quickstart).

```dotenv
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=gemini-3.8-flash
GEMINI_TIMEOUT_SECONDS=20
GROQ_API_KEY=your-groq-key
GROQ_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_ENABLED=true
GROQ_TIMEOUT_SECONDS=35
```

The model names above are the project's configured defaults, not a guarantee of availability for every account. Use models available to your account that support the structured-output request used by `backend/app/llm.py`. Provider quotas and availability can change. If not using Groq, leave its key blank and keep `GROQ_FALLBACK_ENABLED=false`.

Gemini is primary. Supported transient failures such as timeouts and HTTP 503 can trigger one Groq attempt. Missing/invalid Gemini credentials, malformed outputs and valid human-review decisions do not trigger fallback. Groq is not a standalone primary mode in the current code.

### Database settings

For the simplest setup, keep:

```dotenv
PORT=4000
MONGODB_URI=
MONGODB_DATABASE=acme_collections
```

An empty `MONGODB_URI` selects TinyDB. On first startup, the supplied `data/` files are loaded automatically into `backend/storage/workspace.json`. No separate database server or seed command is needed. Restarting preserves changes.

Optional MongoDB setup, from the application root:

```sh
docker compose up -d
```

Then set `MONGODB_URI=mongodb://127.0.0.1:27017` in `backend/.env` and restart the backend. The Compose service exposes MongoDB only on localhost and stores data in a named volume. A new database is seeded automatically. Existing TinyDB data is **not** migrated into MongoDB. Use `docker compose stop` to stop MongoDB without removing its volume.

Run **one backend process/worker only**, with either database. The small workspace is stored as one document and application mutations use an in-process lock. Multiple workers or replicas are not supported. A legacy `backend/storage/workspace.db`, if present on an older installation, is imported into an empty TinyDB workspace on first startup; fresh clones do not need that file.

### Database design

Both backends store one workspace document. MongoDB uses the `workspace` collection with `_id: "demo"`; TinyDB holds an equivalent document in its JSON file. The following are nested data groups, not separate relational tables:

| Data | Purpose and links |
|---|---|
| `organization` | Contacts, bank/payment details and billing policies |
| `customers` | Customer IDs, names/aliases, contact email and reminder pauses |
| `invoices` | `customer_id`, invoice number/dates/currency, subtotal, cash paid, TDS adjustment and current balance/status |
| `transactions` | Imported bank rows, matching information and approval/rejection state |
| `payments` | Approved allocations linking `transaction_id` and `invoice_number`, with separate cash and TDS values |
| `conversations` | Customer-linked threads with nested incoming/outgoing messages, threading IDs, agent decisions and delivery information |
| `mailbox` / `activity` | Inbox checkpoints/deduplication metadata and recorded application actions |

One customer can have many invoices and conversations; one bank transaction can fund several invoice allocations. Mutations validate a copied state under a lock before persistence. TinyDB atomically replaces the JSON file; MongoDB replaces the workspace document. This is intentionally a small-data prototype, not a multi-worker storage design. To back up TinyDB, stop the backend first and copy `backend/storage/workspace.json` to a private location; it can contain real email content. A source-code clone starts with fresh sample data, not your existing mailbox history.

## 5. Start and open the application

From the application root:

```sh
npm run dev
```

Keep the terminal open. It starts the FastAPI server and Vite frontend together.

- Application: [http://127.0.0.1:5173](http://127.0.0.1:5173)
- Backend API documentation: [http://127.0.0.1:4000/docs](http://127.0.0.1:4000/docs)
- Read-only status: [http://127.0.0.1:4000/api/mail/status](http://127.0.0.1:4000/api/mail/status)

Check that the dashboard displays 27 invoices and nine customers on a fresh database. No login is implemented; this is a local internal prototype.

Press **Ctrl+C** in the terminal to stop both services. Inbox polling stops when the backend stops. Restart with the same command; do not start a second copy while one is already running.

For separate terminals, run `npm run dev:backend` in one and `npm run dev -w frontend` in another, both from the application root. Changing `PORT` also requires updating the API target in `frontend/vite.config.js`. Use the URL printed by Vite if the default frontend port is occupied; preferably stop the previous instance instead.

### First-run verification

Before testing mail, confirm these checks:

1. The terminal reports both API and WEB servers running.
2. `/docs` opens on port 4000, and the frontend opens on Vite's printed URL.
3. The Invoices and Customers pages contain 27 invoices and nine customers on a fresh database.
4. In Bank reconciliation, load the sample statement. Suggestions appear without email or LLM credentials.
5. Approve a chosen receipt and confirm its invoice balance changes; refresh the page to confirm persistence. This changes your demo data; reset if you need the original fixtures for the walkthrough below.

`/api/mail/status` confirms the app's configuration/check state; opening it alone does **not** prove Gmail authentication or model availability. Those are verified by the real email walkthrough. The documented addresses serve the same device running the app; they are not a deployment URL for another computer.

## 6. Test real email collection

1. Open **Customers**. Edit Kestrel Foods' email to match `TEST_PAYER_EMAIL` and save it.
2. Open **Collections > Prepare reminder** and select Kestrel.
3. Select one or more sent, unpaid invoices. All eligible invoices are selected initially; paid and draft invoices are excluded. Invoices not yet overdue are explicitly marked in the email.
4. Click **Preview email**. Check the recipient, invoice amounts, due dates and total. A blocked reason must be resolved before sending.
5. Click **Send reminder**. One real SMTP email is sent for the selected invoices. SMTP acceptance is recorded, but does not guarantee final inbox delivery. Inspect the payer inbox, including Spam if necessary.
6. From the payer inbox, reply to that email: `What is our outstanding balance? Please share payment details.` Use the normal reply action to preserve threading headers.
7. Click **Check inbox now**, or enable polling as described below. The agent imports relevant mail, builds customer-specific context and attempts a validated response.
8. Open the conversation to inspect the incoming email and outgoing delivery status. For fresh Kestrel data, the total sent/unpaid balance is INR 118,000, even if the original reminder selected only one invoice.
9. Reply with `Can you extend the payment deadline?` to test **Needs attention**. A model draft may be available or may be empty; write/edit the reply and click **Send reviewed email**. No extension or invoice change is applied automatically.
10. Reply with `We have paid` to test acknowledgement. The invoice must remain unpaid until bank reconciliation approval.

Automatic responses require validated sender/thread evidence, an allowed routine intent, confidence >=0.9 and approved wording. Python inserts financial facts from current database records. Review and generation failures are not silently treated as successful sends. **Retry AI processing** may send a validated response and is only available for eligible failures before a send attempt.

### Automatic inbox processing

After configuring your test accounts, set:

```dotenv
MAIL_POLL_ENABLED=true
MAIL_POLL_INTERVAL_SECONDS=60
```

Restart after configuration changes for a predictable setup. The worker waits 60 seconds before its first check and between completed cycles; processing time adds to that interval. New relevant messages may cause real automatic replies to the configured payer. The manual button remains available, and a lock prevents overlapping inbox checks.

The dashboard refreshes every 10 seconds. It preserves typed replies and pauses refresh during payment review. Existing failed generations are not automatically retried on every poll.

The example configuration defaults polling to false so merely starting a fresh installation does not process a real mailbox. Enabling it is part of live email setup, not required for offline reconciliation.

### Out-of-office notices and bounces

- A validated out-of-office notice pauses reminders until its recorded resume date without altering invoice due dates. Customer cards show the pause and a manual **Clear reminder pause** action.
- Explicit dates and supported day/month forms such as `5Jan` are parsed locally and compared with the model's proposed date. Missing years use the next occurrence on/after the received timestamp. `Till`, `until` and `through` include that day; reminders resume the following day in Asia/Kolkata. Relative/uncertain dates, payment-extension language and dates beyond the 90-day limit require review. Expiry permits sending again; it does not send a reminder itself.
- Structured delivery reports are linked using original message ID and recipient. Confirmed failures require additional Gmail-style authentication evidence. Failures and unverified reports appear in Needs attention; delays are informational. Delivery reports never receive AI replies. Confirmed failures block further reminders to the failed address. SMTP acceptance remains in history alongside later delivery events.

## 7. Test bank reconciliation

Open **Bank reconciliation** and choose **Load sample statement**, or upload a CSV with these columns:

```csv
txn_date,narration,reference,credit,debit,currency
2026-09-05,IMPS/KESTREL FOODS/INV 1004,624811204417,59000,,INR
```

Use `YYYY-MM-DD` dates and plain numeric amounts without currency symbols or thousands separators. Exactly one of credit/debit must be positive per row. Quote CSV fields containing commas. Imports accept 1â€“2,000 rows with a 2 MB request cap. Duplicate rows are skipped. Outgoing debits are excluded from invoice allocation.

Each receipt shows a suggestion or no match, a reason and, where applicable, candidate allocations. Matching scores rank evidence; they are not calibrated probabilities. Open **Review** to approve, reject or manually select an eligible invoice. **Nothing changes until approval.**

Useful checks on a fresh dataset:

| Receipt | Review action | Expected result |
|---|---|---|
| Kestrel INR 59,000 | Approve INV-1004 | Invoice paid; subsequent customer context/reminders exclude it |
| Quillon INR 324,000 | Choose INV-1007 and TDS closure | INR 324,000 cash + INR 30,000 TDS; invoice paid |
| Orbis INR 200,000 | Apply to INV-1013; leave balance open | INR 272,000 remains |
| Saffron INR 194,700 | Select the combined allocation for INV-1010/1011/1012 | Three invoices close together after approval |

TDS follows the assignment's INR rule: a deduction of 10% of invoice subtotal. It is recorded separately from cash. A customer email alone never approves payment. Combined allocations must sum exactly to the receipt and are validated before a single workspace mutation. Partially paid invoices remain sent/unpaid.

## 8. Reset and persistence

**Reset demo data** restores the original organization, customer and invoice records and clears conversations, stored email copies, imported transactions, payments, pauses and activity. Edited customer addresses revert to `example.com`; set your chosen customer back to the test address before another live test.

Reset does not delete actual Gmail messages, credentials in `.env`, or local logs. Mailbox account, UIDVALIDITY and last-scanned UID/deduplication metadata are retained to avoid replaying old mail. If UIDVALIDITY changes, the old UID checkpoint is discarded; because reset removed message history, older mail may be imported again. For a clean demonstration, send and reply to a new reminder after resetting.

## 9. How matching works

1. Filter to sent/unpaid invoices with matching currency and invoice date no later than the receipt.
2. Extract bounded `INV` references from narration/reference fields; reject unknown, ineligible or cross-customer references.
3. Normalize payer names and aliases. Compare token sets using `SequenceMatcher` and character 3/4-gram TF-IDF cosine similarity. The fuzzy payer score is 55% token similarity + 45% character similarity; exact normalized-name phrases receive 1.0.
4. Without an invoice reference, require payer score >=0.72 and a >=0.12 lead over the runner-up. A reference with conflicting strong payer evidence requires review.
5. Compare current balances, assignment-rule TDS and partial amounts. Individual base score is 70 with an invoice reference, otherwise round(55 Ã— payer score). Add 25 for exact balance, 20 for TDS or 5 for partial payment. Top candidates less than 10 points apart remain ambiguous.
6. Search combinations locally using integer cents. Searches are bounded to 18 invoices, 100,000 visited nodes and 20 results; incomplete searches are flagged. Explicit multi-invoice references require all referenced invoices and support suggested allocations with at most one residual partial invoice. Alternative valid splits require human selection.

No bank statement is sent to an LLM. These heuristics are tested against sample and synthetic cases, not a guarantee of accuracy on unseen statements.

### Scoring and assumptions

The single-invoice score is capped at 99. A unique, complete unreferenced multi-invoice allocation scores 80; a unique, complete settlement of all explicit references scores 95. Ambiguous or truncated combined results, and explicit-reference partial alternatives, score 0 and require review. These combined scores use a separate rule from individual invoice scores.

Without an invoice reference, matching expects a recognizable company name or alias in the narration. Personal-account, parent-company or third-party payments cannot reliably identify the customer from amount alone; use manual review or a future verified payer mapping. This is not bank-account ownership verification. An explicit invoice reference can still identify a candidate when payer-name evidence is weak.

A receipt is allocated within one customer and currency. The algorithm does not infer foreign-exchange conversion, bank fees or overpayments. Unreferenced combinations cover full/TDS closures; multiple explicit references can include at most one residual partial invoice. Shared amounts, conflicting references and incomplete searches deliberately remain uncertain. Token comparisons handle reordered/noisy narration while character TF-IDF supplies spelling-fragment evidence; neither alone establishes payment identity.

## 10. Project structure

```text
backend/
  app/
    main.py                 API routes and request validation
    store.py                TinyDB/MongoDB access, seeding and atomic mutations
    agent.py                Context, polling, review routing and response delivery
    llm.py                  Gemini/Groq structured classification
    inbox.py                IMAP retrieval, checkpoints and sender/thread association
    mail.py                 Legacy single-invoice SMTP test
    reminders.py            Selected-invoice preview and SMTP reminder sending
    availability.py         Out-of-office date validation and reminder pauses
    bounces.py              Delivery-report parsing and association
    conversation_view.py    Normal/review queue derivation
    matching.py             Payer/reference/amount matching
    allocations.py          Combined allocation search and approval
    logic.py                Invoice/payment rules and transaction identifiers
    services.py             CSV import and legacy simulated-email flows
  tests/                    Isolated regression tests
  .env.example              Credential placeholders and configuration defaults
  requirements.txt          Supported direct dependency ranges
  requirements.lock.txt     Pinned dependency snapshot for reproducible installs
  run.py
frontend/
  src/main.jsx              Workspace pages and review screens
  src/ReminderComposer.jsx  Customer/invoice selection and reminder preview
  src/style.css
  vite.config.js            Frontend-to-backend proxy
scripts/backend.mjs         npm launcher for Python; no business logic
data/                       Original fictional assignment data
docker-compose.yml          Optional local MongoDB
package.json                Shared npm commands and frontend workspace
package-lock.json           Locked JavaScript dependencies
.gitignore                  Excludes secrets and generated local files
README.md                   Setup, operation and limitations
explanation.pdf             Three-page architecture and implementation report
```

The application root is the portable project: keep `backend/`, `frontend/`, `scripts/`, `data/` and the root configuration files together. The backend separates API orchestration, database access, mail transport and LLM integration into modules under `backend/app/`; these run within one FastAPI process. The small React prototype keeps its pages in `main.jsx` and the reminder composer in a separate component.

Generated local folders are intentionally ignored: `backend/.venv/`, `backend/storage/`, `node_modules/`, `frontend/node_modules/`, `frontend/dist/`, `.npm-cache/`, `debug/`, `__pycache__/` and `.setup-check/`. They are not required in a clone. Never include `backend/.env` in a handoff. Keep `data/`: first-start database seeding depends on it.

Before sharing, replace the clone URL placeholder above with your repository URL. Verify that the source, example configuration, data and lockfiles are committed; excluded local files are recreated during setup.

## 11. API reference

Interactive request schemas are available at `/docs` while the backend runs. All paths below start with `/api`.

| Method | Path | Purpose / body |
|---|---|---|
| GET | `/state` | Workspace, recomputed pending suggestions and mailbox status |
| GET | `/mail/status` | Polling/check status; no credentials |
| POST | `/mail/check` | Import and process mail; may send real replies |
| PATCH | `/customers/{id}` | Update contact address: `{"email":"payer@example.com"}` |
| GET | `/customers/{id}/reminder-preview` | Preview; optional repeated `invoice_numbers=INV-1004` query parameters |
| POST | `/customers/{id}/reminder` | Send preview: `{"preview_id":"...","invoice_numbers":["INV-1004"]}` |
| POST | `/customers/{id}/clear-reminder-pause` | Clear pause and record activity |
| POST | `/conversations/{id}/retry-generation` | Retry an eligible failed generation; may send a real reply |
| POST | `/conversations/{id}/send` | `{"text":"..."}`; real SMTP for imported mail, local save for simulations; delivery reports cannot receive replies |
| POST | `/statements` | `{"sample":true}` or `{"csv":"..."}` |
| POST | `/transactions/{id}/review` | Approve: `{"action":"approve","invoice":"INV-1004","mode":"partial"}`; mode can be `tds`; reject: `{"action":"reject"}` |
| POST | `/transactions/{id}/review` | Combined: `{"action":"approve","allocations":[{"invoice":"INV-1010","cash":82600,"tds":0},{"invoice":"INV-1011","cash":41300,"tds":0}]}`; sum must equal the selected receipt |
| POST | `/reset` | Restore demo business data; retains mailbox tracking |
| POST | `/reminders` | Legacy simulation only; no SMTP; no longer the Collections reminder button |
| POST | `/conversations/{id}/reply` | Legacy simulated incoming text: `{"message":"..."}`; no IMAP/LLM |
| POST | `/mail/test-reminder/{invoice_number}` | Legacy real single-invoice test; changes that customer's email to TEST_PAYER_EMAIL; prefer the preview workflow |

Validation errors return HTTP 400 with an `error` message. Previewing does not send mail. Sending revalidates invoice selection/balances and active pauses. Delivery reservations block repeated identical sends after acceptance or uncertain delivery.

## 12. Tests and build

From the application root after installing dependencies:

```sh
npm test
npm run build
```

Tests use temporary databases and mocked external services, including matching, combined/TDS/partial allocations, duplicate approvals, reminder selection, review drafts, polling, availability dates and bounces. They do not establish live Gmail/provider availability. `npm run build` creates `frontend/dist`; it does not start or bundle the Python API. `npm run dev` is the documented local serving method; production deployment is not packaged.

### Verification scope

The pinned Python dependencies were installed in an isolated local Python 3.12 environment, all 66 backend tests passed, and the frontend production build passed. These checks were performed on Windows; a separate-device/macOS/Linux setup and live third-party account availability have not been certified. Tests do not send actual customer email. The OS-specific commands above use the paths selected by the development launcher.

## 13. Troubleshooting

| Symptom | What to check |
|---|---|
| Browser refuses connection | Keep `npm run dev` running; inspect both API and WEB terminal output. Check Vite's printed URL. |
| Address/port already in use | Stop the earlier instance; run one backend only. Do not run the combined command alongside separate copies. |
| Frontend opens but API fails | Confirm port 4000 and `frontend/vite.config.js` agree; check backend startup errors. |
| Python launcher/venv missing | Recreate `backend/.venv` with installed Python and install `backend/requirements.lock.txt`. |
| `npm ci` fails | Run it from the application root with `package-lock.json` present and development dependencies enabled. Check Node/npm versions and registry/network access; do not copy another device's node_modules. |
| Python package installation fails | Confirm Python 3.12 and pip belong to `backend/.venv`; check package-index/network access. Recreate the environment on the destination device rather than copying it. |
| MongoDB connection refused | Start Docker and `docker compose up -d`, or leave MONGODB_URI blank for TinyDB; restart the backend after changing it. |
| Gmail authentication rejected | Verify company address, 2-Step Verification and App Password, not the normal login password. Check account restrictions. |
| Reminder blocked by recipient restriction | Customer contact email must equal TEST_PAYER_EMAIL. Use a different inbox from MAIL_USERNAME. |
| No new reply appears | Reply from the configured payer to a real reminder; check Last checked/error, polling setting and threading. Previously scanned emails are skipped. |
| Polling enabled but nothing happens | Backend must stay running. First check waits one interval; long processing adds delay. Inspect `/api/mail/status`. |
| Gemini configuration/access error | Check key and available model ID; fallback does not hide primary configuration errors. |
| Model timeout, quota or malformed response | Inspect Needs attention. Eligible failures can be retried explicitly; review decisions are not automatically overridden. |
| Human-review item has no draft | Empty drafts are allowed for review. Write a reply; this alone is not a provider failure. |
| SMTP failed/unknown or message still sending | Inspect company Sent folder and payer inbox before retrying. A crash can leave a durable sending reservation; do not bypass it blindly. |
| Out-of-office customer cannot be selected | Wait until the resume date or use the logged Clear reminder pause action after reviewing the email. |
| Bounce blocks reminders | Inspect the report and correct the recipient address. No automatic resend or verified-address block-clear workflow exists. |
| Payment has no unique match | Inspect candidates and choose manually. Equal amounts, unknown payers and incomplete searches are deliberately not guessed. |

## 14. Scope and limitations

- Local prototype without login/production authorization; do not expose it publicly. Credentials stay in the backend, but a full security audit has not been performed.
- Gmail-only transport, one configured test payer and one backend worker. Unrelated unknown senders are skipped; unknown senders referencing existing threads go to review. Authentication/report verification is conservative and not universal.
- The LLM receives organization data, this customer's eligible invoices and recent thread text. Email content is untrusted; output validation and restricted automatic rendering limit actions, but prompt-injection immunity is not claimed. Free-form human-reviewed text is not fully checked against database facts.
- Reminders are initiated through the dashboard. Inbox processing is automatic when enabled; recurring overdue-reminder scheduling is not implemented.
- Payment-email-to-bank-transaction linking (bonus 1) is not implemented. Combined allocation and out-of-office/bounce handling are implemented with the documented limits.
- Arbitrary multiple-partial allocations are not inferred; local embeddings are not used. Scores are heuristic and should be evaluated further on unseen data.
- Availability uses Asia/Kolkata and app import timestamps; relative dates and early-return automation are not supported. No dedicated bounce-resolution/clear-block workflow is implemented.
- For submission, also provide the public repository URL, required `explanation.pdf` and unlisted demonstration video. This README is not a substitute for those deliverables.
