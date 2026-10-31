"""Owner-safe requests and decisions; never executes work or makes a payment."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .access import AccessError, authorize_property
from .admin import OwnerAdmin, identity, record
from .calculations import number
from .models import OwnerRequest, Property, PropertyGrant, utcnow


def publish(db, actor, data):
    account = OwnerAdmin(db, actor).account(data.get('account_id'))
    prop = db.get(Property, identity(data.get('property_id')))
    title, summary = data.get('title'), data.get('summary')
    if not isinstance(title,str) or not title.strip() or len(title)>120 or not isinstance(summary,str) or not summary.strip() or len(summary)>1000:
        raise AccessError('Enter a short title and an owner-safe description.',400)
    if data.get('owner_safe_reviewed') is not True:
        raise AccessError('Review the text for the owner before publishing.',400)
    if not prop or not prop.active:
        raise AccessError('Property unavailable.',404)
    day = utcnow().astimezone(ZoneInfo(prop.timezone)).date()
    grant = db.scalar(select(PropertyGrant).where(PropertyGrant.account_id==account.id,PropertyGrant.property_id==prop.id,
        PropertyGrant.revoked_at.is_(None),PropertyGrant.reporting_from<=day,
        (PropertyGrant.reporting_until.is_(None)|(PropertyGrant.reporting_until>day))))
    if not grant:
        raise AccessError('This home needs current reporting access for this owner.',409)
    amount = None if data.get('amount') in (None,'') else number(data['amount'])
    if data.get('amount') not in (None,'') and (amount is None or amount<0 or amount!=amount.quantize(number('.01'))):
        raise AccessError('Enter a valid USD amount with at most two decimal places.',400)
    row = OwnerRequest(account_id=account.id,property_id=prop.id,title=title.strip(),summary=summary.strip(),amount=amount,
                       published_by=actor,reporting_date=day)
    db.add(row);record(db,actor,'request.published',row,property_id=prop.id)
    return {'id':row.id}


def withdraw(db, actor, data):
    account = OwnerAdmin(db,actor).account(data.get('account_id'))
    row = db.get(OwnerRequest,identity(data.get('request_id')))
    if not row or row.account_id!=account.id:
        raise AccessError('Request unavailable.',404)
    row.withdrawn_at=utcnow();record(db,actor,'request.withdrawn',row)
    return {'withdrawn':True}


def owner_list(db,user_id,account_id,property_ids):
    result=[]
    for row in db.scalars(select(OwnerRequest).where(OwnerRequest.account_id==account_id,OwnerRequest.property_id.in_(property_ids),
                            OwnerRequest.withdrawn_at.is_(None)).order_by(OwnerRequest.published_at.desc())):
        try:authorize_property(db,user_id,account_id,row.property_id,row.reporting_date,row.reporting_date+timedelta(days=1))
        except AccessError:continue
        prop=db.get(Property,row.property_id)
        result.append({'id':row.id,'property':prop.name,'title':row.title,'summary':row.summary,
            'amount':float(row.amount) if row.amount is not None else None,'response':row.response,
            'published_at':row.published_at.isoformat(),'responded_at':row.responded_at.isoformat() if row.responded_at else None})
    return result


def respond(db,user_id,request_id,response):
    if response not in ('approved','discuss'):
        raise AccessError('Choose approve or discuss with the team.',400)
    row=db.scalar(select(OwnerRequest).where(OwnerRequest.id==request_id).with_for_update())
    if not row or row.withdrawn_at:
        raise AccessError('Request unavailable.',404)
    authorize_property(db,user_id,row.account_id,row.property_id,row.reporting_date,row.reporting_date+timedelta(days=1))
    if row.response is not None:
        raise AccessError('This request has already received a response. Reload to see it.',409)
    row.response=response;row.responded_by=user_id;row.responded_at=utcnow()
    record(db,f'owner:{user_id}','request.responded',row,response=response)
    return {'saved':True}
