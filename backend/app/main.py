"""HTTP routes. Run with: python -m uvicorn app.main:app --port 4000 --reload."""
import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from .store import BACKEND, Store, initial_state
from .logic import approve
from . import services

load_dotenv(BACKEND / '.env')


class CustomerEmail(BaseModel):
    email: str


class ReminderConfirmation(BaseModel):
    invoice_numbers: list[str] | None = Field(default=None, min_length=1, max_length=100)
    preview_id: str = Field(min_length=64, max_length=64)


class CustomerReply(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class ReviewedDraft(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class StatementUpload(BaseModel):
    sample: bool = False
    csv: str | None = None


class AllocationRow(BaseModel):
    balance_due: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    invoice: str
    cash: float = Field(gt=0, allow_inf_nan=False)
    tds: float = Field(default=0, ge=0, allow_inf_nan=False)


class PaymentReview(BaseModel):
    allocations: list[AllocationRow] | None = Field(default=None, max_length=18)
    action: str
    invoice: str | None = None
    mode: str | None = None


def create_app(store_override=None):
    @asynccontextmanager
    async def lifespan(application):
        application.state.store = store_override or Store(mongo_uri=os.getenv('MONGODB_URI'))
        from .agent import Agent
        application.state.agent = Agent(application.state.store)
        if store_override is None:
            application.state.agent.start()
        yield
        if store_override is None:
            application.state.agent.close()
            application.state.store.close()

    application = FastAPI(title='Acme Collections API', version='0.2.0', lifespan=lifespan)

    @application.middleware('http')
    async def limit_body(request, call_next):
        if request.method in ('POST', 'PATCH', 'PUT'):
            # Count streamed bytes as well as Content-Length to enforce the same 2 MB cap.
            chunks, length = [], 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > 2 * 1024 * 1024:
                    return JSONResponse(status_code=400, content={'error': 'Request body exceeds 2 MB'})
                chunks.append(chunk)
            request._body = b''.join(chunks)
        return await call_next(request)

    @application.exception_handler(ValueError)
    async def invalid_value(request, error):
        return JSONResponse(status_code=400, content={'error': str(error)})

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse(status_code=400, content={'error': 'Invalid request: ' + '; '.join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors())})

    def store():
        return application.state.store

    @application.get('/api/state')
    def get_state():
        from .conversation_view import prepare_conversation
        state = store().get()
        from .logic import suggest
        from .availability import pause_active
        for customer in state['customers']:
            customer['reminders_paused']=pause_active(customer)
        for transaction in state['transactions']:
            if transaction['status'] == 'pending':
                transaction['suggestion'] = suggest(transaction, state['invoices'], state['customers'])
        state['conversations'] = [prepare_conversation(c) for c in state['conversations']]
        return {**state, 'database': store().database_name,
                'mode': 'Live SMTP/IMAP · demo simulation also available',
                'mail_status': application.state.agent.status()}

    @application.post('/api/mail/check')
    def check_inbox():
        return application.state.agent.check()

    @application.get('/api/mail/status')
    def mail_status():
        return application.state.agent.status()

    @application.post('/api/conversations/{identifier}/retry-generation')
    def retry_generation(identifier: str):
        return application.state.agent.retry_generation(identifier)

    @application.post('/api/customers/{customer_id}/clear-reminder-pause')
    def clear_reminder_pause(customer_id: str):
        def clear(state):
            customer=next((c for c in state['customers'] if c['customer_id']==customer_id),None)
            if customer is None:raise ValueError('Customer not found.')
            customer.pop('reminder_pause',None)
            services.log(state, 'Reminder pause manually cleared for '+customer['name'])
        store().mutate(clear)
        return {'message':'Reminder pause cleared.'}

    @application.patch('/api/customers/{customer_id}')
    def edit_customer(customer_id: str, body: CustomerEmail):
        email = body.email.strip()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise ValueError('Enter a valid email address.')
        def update(state):
            customer = next((c for c in state['customers'] if c['customer_id'] == customer_id), None)
            if customer is None:
                raise ValueError('Customer not found')
            customer['contact_email'] = email
        store().mutate(update)
        return {'ok': True}

    @application.post('/api/reset')
    def reset():
        agent = application.state.agent
        if not agent.lock.acquire(blocking=False):
            raise ValueError('Wait for inbox processing before resetting.')
        try:
            # Keep retrieval history to prevent reset from replaying old real emails.
            store().mutate(lambda state: state.update(initial_state()))
        finally:
            agent.lock.release()
        return {'ok': True}

    @application.post('/api/reminders')
    def reminders():
        return store().mutate(services.create_reminders)

    @application.get('/api/customers/{customer_id}/reminder-preview')
    def reminder_preview(customer_id: str, invoice_numbers: list[str] | None = Query(default=None)):
        from dotenv import dotenv_values
        from .reminders import preview
        return preview(store().get(), customer_id, dotenv_values(BACKEND / '.env'), invoice_numbers)

    @application.post('/api/customers/{customer_id}/reminder')
    def customer_reminder(customer_id: str, body: ReminderConfirmation):
        from .reminders import send
        agent = application.state.agent
        if not agent.lock.acquire(blocking=False):
            raise ValueError('Wait for current mail processing to finish.')
        try:
            return send(store(), customer_id, body.preview_id, body.invoice_numbers)
        finally:
            agent.lock.release()

    @application.post('/api/mail/test-reminder/{invoice_number}')
    def test_reminder(invoice_number: str):
        from .mail import send_test_reminder
        return send_test_reminder(store(), invoice_number)

    @application.post('/api/conversations/{identifier}/reply')
    def reply(identifier: str, body: CustomerReply):
        text = body.message.strip()
        if not text:
            raise ValueError('Enter a message (maximum 5,000 characters).')
        store().mutate(lambda state: services.process_reply(state, identifier, text))
        return {'ok': True}

    @application.post('/api/conversations/{identifier}/send')
    def send(identifier: str, body: ReviewedDraft):
        text = body.text.strip()
        if not text:
            raise ValueError('Draft must contain 1–5,000 characters')
        thread = next((c for c in store().get()['conversations'] if c['id']==identifier), None)
        if thread and any(m.get('transport')=='imap' for m in thread['messages']):
            application.state.agent.reviewed_send(identifier, text)
        else:
            store().mutate(lambda state: services.send_reviewed_reply(state, identifier, text))
        return {'ok': True}

    @application.post('/api/statements')
    def statements(body: StatementUpload):
        return store().mutate(lambda state: services.import_statement(state, body.csv, body.sample))

    @application.post('/api/transactions/{identifier}/review')
    def review(identifier: str, body: PaymentReview):
        def update(state):
            if body.action == 'reject':
                transaction = next((t for t in state['transactions'] if t['id'] == identifier), None)
                if transaction is None or transaction['status'] != 'pending':
                    raise ValueError('Transaction unavailable')
                transaction['status'] = 'rejected'
            elif body.action == 'approve':
                if body.allocations:
                    from .allocations import approve_allocation
                    approve_allocation(state, identifier, [row.model_dump() for row in body.allocations])
                    services.log(state, 'Combined bank payment approved')
                    return
                if body.mode not in ('partial', 'tds'):
                    raise ValueError('Invalid settlement mode')
                approve(state, identifier, body.invoice, body.mode)
            else:
                raise ValueError('Invalid action')
            services.log(state, f'Bank transaction {body.action}d')
        store().mutate(update)
        return {'ok': True}

    return application


app = create_app()
