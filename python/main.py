import json
import logging

from flask import Flask, render_template, request, jsonify
from datetime import datetime
from dateutil.parser import parse
from decimal import Decimal

import settings


from pynYNAB.Client import nYnabClient, nYnabConnection
from pynYNAB.schema.Entity import Entity, ComplexEncoder, Base, AccountTypes
from pynYNAB.schema.budget import Account, Transaction, Payee
from pynYNAB.schema.roots import Budget
from pynYNAB.schema.types import AmountType

from sqlalchemy.sql.expression import select, exists, func


app = Flask(__name__, template_folder='../html', static_folder='../static')
app.config['DEBUG'] = settings.flask_debug

log = logging.getLogger(__name__)

if settings.sentry_dsn:
    from raven.contrib.flask import Sentry
    sentry = Sentry(app)

@app.route('/')
def route_index():
    return 'hello world'

@app.route('/webhook', methods=['POST'])
def route_webhook():
    global expectedDelta
    data = json.loads(request.data.decode('utf8'))

    expectedDelta = 1

    if data['type'] == 'transaction.created':
        ynab_connection = nYnabConnection(settings.ynab_username, settings.ynab_password)
        ynab_client = nYnabClient(nynabconnection=ynab_connection, budgetname=settings.ynab_budget, logger=log)
        ynab_client.sync()

        accounts = {x.account_name: x for x in ynab_client.budget.be_accounts}
        payees = {p.name: p for p in ynab_client.budget.be_payees}

        def getaccount(accountname):
            try:
                log.debug('searching for account %s' % accountname)
                return accounts[accountname]
            except KeyError:
                log.error('Couldn''t find this account: %s' % accountname)
                exit(-1)

        def getpayee(payeename):
            try:
                log.debug('searching for payee %s' % payeename)
                return payees[payeename]
            except KeyError:
                global expectedDelta
                log.debug('Couldn''t find this payee: %s' % payeename)
                payee=Payee(name=payeename)
                ynab_client.budget.be_payees.append(payee)
                expectedDelta=2
                return payee

    	def containsDuplicate(transaction, session):
    		return session.query(exists()\
 		#Due to a bug with pynynab we need to cast the amount to an int for this comparison. This should be removed when bug #38 is fixed https://github.com/rienafairefr/pynYNAB/issues/38
      		#  .where(Transaction.amount==transaction.amount)\
        	.where(Transaction.entities_account_id==transaction.entities_account_id)\
      		.where(Transaction.date==transaction.date.date())\
        	.where(Transaction.imported_payee==transaction.imported_payee)\
        	.where(Transaction.source==transaction.source)\
        	).scalar()

        entities_account_id = getaccount(settings.ynab_account).id
        payee_name = ''
        if((data['data']['merchant'] is None) and (data['data']['counterparty'] is not None) and (data['data']['counterparty']['number'] is not None)):
            payee_name = data['data']['counterparty']['number']
        else:
            payee_name = data['data']['merchant']['name']

        entities_payee_id = getpayee(payee_name).id

        # Try and get the suggested tags
        try:
            suggested_tags = data['data']['merchant']['metadata']['suggested_tags']
        except (KeyError, TypeError):
            suggested_tags = ''

        # Try and get the emoji
        try:
            emoji = data['data']['merchant']['emoji']
        except (KeyError, TypeError):
            emoji = ''

        transaction = Transaction(
            entities_account_id=entities_account_id,
            amount=Decimal(data['data']['amount']) / 100,
            date=parse(data['data']['created']),
            entities_payee_id=entities_payee_id,
            imported_date=datetime.now().date(),
            imported_payee=payee_name,
            memo="%s %s" % (emoji, suggested_tags),
            source="Imported"
        )

        if containsDuplicate(transaction, ynab_client.session):
            log.debug('Duplicate transaction found')
        else:
            log.debug('Appending transaction')
            ynab_client.budget.be_transactions.append(transaction)
            ynab_client.push(expectedDelta)

        return jsonify(data)
    else:
        log.warning('Unsupported webhook type: %s' % data['type'])

    return ''

if __name__ == "__main__":
    app.run(host="0.0.0.0")
