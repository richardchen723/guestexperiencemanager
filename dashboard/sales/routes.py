"""Sales permissions, bounded JSON, CSRF, and transaction boundaries."""
import json
import logging
import secrets
from functools import wraps
from flask import Blueprint, g, jsonify, render_template, request, session, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from itsdangerous import URLSafeTimedSerializer, BadSignature
from flask import current_app
from dashboard.auth.api_keys import _get_api_key_from_request, verify_api_key
from dashboard.auth.decorators import check_feature_access
from dashboard.auth.features import user_can_access_feature
from dashboard.auth.models import get_session, get_all_users, get_user_by_id
from dashboard.auth.session import get_current_user
from database.models import Listing, get_session as main_session
import dashboard.config as config
from .models import Source, Intake, Activity
from .service import SalesService
from . import validation as v, imports

sales_bp=Blueprint('sales',__name__,url_prefix='/sales')
logger=logging.getLogger(__name__)


@sales_bp.before_request
def access():
    raw=_get_api_key_from_request(request)
    if raw:
        key=verify_api_key(raw)
        if not key: return jsonify(error='Invalid API key.'),401
        issuer=get_user_by_id(key.created_by) if key.created_by else None
        if not issuer or issuer.deleted_at or not user_can_access_feature(issuer,'sales'):
            return jsonify(error='The API key issuer needs active Sales CRM access.'),403
        scope='sales:read' if request.method in ('GET','HEAD') else 'sales:intake'
        if not key.allows_scope(scope): return jsonify(error=f'{scope} scope required.'),403
        if request.method not in ('GET','HEAD') and request.endpoint != 'sales.intake':
            return jsonify(error='Agent keys may capture intake only. Human review is required for CRM decisions.'),403
        if request.endpoint in ('sales.page','sales.export','sales.failures','sales.import_preview'):
            return jsonify(error='Use a human browser session for this operation.'),403
        g.sales_actor=f'api_key:{key.api_key_id}'
        g.sales_name=key.name or f'API key {key.api_key_id}'
        g.sales_user=None
    else:
        denied=check_feature_access('sales')
        if denied is not None: return denied
        user=get_current_user()
        if user.deleted_at: return jsonify(error='Account is inactive.'),403
        g.sales_user=user
        g.sales_actor=f'user:{user.user_id}'
        g.sales_name=user.name or user.email
        if request.method not in ('GET','HEAD','OPTIONS'):
            expected=session.get('sales_csrf','')
            if not expected or not secrets.compare_digest(expected,request.headers.get('X-CSRF-Token','')):
                return jsonify(error='Reload this page before saving; your session changed.'),403
    if request.method not in ('GET','HEAD','OPTIONS'):
        raw=request.stream.read(1024*1024+1)
        if len(raw)>1024*1024: return jsonify(error='Submission exceeds 1 MB.'),413
        try: g.sales_payload=json.loads(raw)
        except (ValueError,UnicodeDecodeError): return jsonify(error='A valid JSON object is required.'),400
        if not isinstance(g.sales_payload,dict): return jsonify(error='A JSON object is required.'),400


@sales_bp.after_request
def private(response):
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response


def reference_data():
    users={u.user_id:{'id':u.user_id,'name':u.name or u.email} for u in get_all_users()
           if u.is_approved and not u.deleted_at and u.email!=config.API_SERVICE_EMAIL and user_can_access_feature(u,'sales')}
    with main_session(config.MAIN_DATABASE_PATH) as db:
        listings={p.listing_id:{'id':p.listing_id,'name':p.internal_listing_name or p.name or str(p.listing_id)} for p in db.query(Listing).all()}
    return users,listings


def endpoint(*, mutation=False, replay=True):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            db=get_session()
            try:
                users,listings=reference_data(); user=g.sales_user
                service=SalesService(db,actor=g.sales_actor,actor_name=g.sales_name,users=users,listings=listings,
                    manager=bool(user and user_can_access_feature(user,'sales_management')),
                    can_export=bool(user and user_can_access_feature(user,'sales_export')))
                service.settings()  # Migration is explicit, never a GET side effect.
                if mutation and replay:
                    result=service.execute(request.endpoint+':'+request.path,g.sales_payload,lambda data:fn(service,data,*args,**kwargs))
                else: result=fn(service,*args,**kwargs)
                if mutation: db.commit()
                return result if isinstance(result,Response) else jsonify(v.jsonable(result))
            except v.SalesError as exc:
                db.rollback(); return jsonify(error=str(exc)),exc.status
            except IntegrityError:
                db.rollback(); return jsonify(error='A related record changed or this name already exists. Reload and retry.'),409
            except Exception:
                db.rollback(); logger.exception('Sales request failed')
                return jsonify(error='The request could not be completed. Your edits were not applied; retry the same action.'),500
            finally: db.close()
        return wrapped
    return decorate


@sales_bp.get('/')
@sales_bp.get('/leads/<int:identity>')
def page(identity=None):
    session.setdefault('sales_csrf',secrets.token_urlsafe(32))
    return render_template('sales/index.html',sales_csrf=session['sales_csrf'],sales_deal_id=identity)


@sales_bp.get('/api/bootstrap')
@endpoint()
def bootstrap(service):
    return {'users':list(service.users.values()),'listings':list(service.listings.values()),'stages':v.STAGES,'markets':v.MARKETS,
            'settings':service.settings_dict(),'can_manage':service.manager,'can_export':service.can_export,
            'current_user_id':g.sales_user.user_id if g.sales_user else None,
            'sources':[{'id':s.id,'name':s.name,'kind':s.kind,'active':s.active,'version':s.version} for s in service.db.scalars(select(Source).order_by(Source.name))]}


@sales_bp.get('/api/leads')
@endpoint()
def listing(service): return service.listing(request.args)

@sales_bp.get('/api/tasks')
@endpoint()
def tasks(service): return service.tasks_listing(request.args)

@sales_bp.get('/api/pipeline')
@endpoint()
def pipeline(service): return service.pipeline(request.args)

@sales_bp.get('/api/reports')
@endpoint()
def reports(service): return service.reports(request.args)

@sales_bp.get('/api/directory/<kind>')
@endpoint()
def directory(service,kind): return service.directory(kind,request.args.get('search'))

@sales_bp.get('/api/leads/<int:identity>')
@endpoint()
def detail(service,identity): return service.detail(identity)

@sales_bp.get('/api/leads/<int:identity>/duplicates')
@endpoint()
def duplicates(service,identity): return service.deal_duplicates(identity)

@sales_bp.get('/api/leads/<int:identity>/activities')
@endpoint()
def activities(service,identity):
    service.deal(identity)
    try: before=int(request.args.get('before',2147483647))
    except ValueError: raise v.SalesError('Invalid activity cursor.') from None
    rows=service.db.scalars(select(Activity).where(Activity.deal_id==identity,Activity.id<before).order_by(Activity.id.desc()).limit(100)).all()
    return {'activities':[{c.name:getattr(a,c.name) for c in a.__table__.columns} for a in rows],'next_before':rows[-1].id if len(rows)==100 else None}

@sales_bp.post('/api/leads')
@endpoint(mutation=True)
def create(service,data): return service.create(data)

@sales_bp.patch('/api/leads/<int:identity>')
@endpoint(mutation=True)
def patch(service,data,identity): return service.patch(identity,data)

# Each action has an explicit transactional, version-checked service method.
def add_action(path,method):
    def action(service,data,identity): return getattr(service,method)(identity,data)
    action.__name__=method
    sales_bp.add_url_rule('/api/leads/<int:identity>/'+path,method,endpoint(mutation=True)(action),methods=['POST'])

for path,method in [('activities','add_activity'),('tasks','task_action'),('relationships','relationship'),
                    ('permissions','permission'),('qualification','qualify'),('assessment','assess'),
                    ('transition','transition'),('proposals','create_proposal'),('proposal-actions','proposal_action'),
                    ('sign','sign'),('onboarding','onboarding')]: add_action(path,method)

@sales_bp.patch('/api/records/<kind>/<int:identity>')
@endpoint(mutation=True)
def record_edit(service,data,kind,identity): return service.edit_record(kind,identity,data)

@sales_bp.post('/api/merge')
@endpoint(mutation=True)
def merge(service,data): return service.merge(data)

@sales_bp.post('/api/sources')
@endpoint(mutation=True)
def source(service,data): return service.source_update(data)

@sales_bp.patch('/api/settings')
@endpoint(mutation=True)
def settings(service,data): return service.update_settings(data)

@sales_bp.post('/api/intake')
@endpoint(mutation=True)
def intake(service,data): return imports.ingest(service,data)

@sales_bp.get('/api/failures')
@endpoint()
def failures(service):
    service.require_manager()
    rows=service.db.scalars(select(Intake).where(Intake.status=='failed').order_by(Intake.id).limit(100))
    return {'failures':[{'id':r.id,'source_id':r.source_id,'external_id':r.external_id,'error':r.error,'original':r.original} for r in rows]}

@sales_bp.post('/api/failures/<int:identity>/retry')
@endpoint(mutation=True)
def retry(service,data,identity): return imports.retry(service,identity,data)

@sales_bp.post('/api/import/preview')
@endpoint()
def import_preview(service):
    data=g.sales_payload; result=imports.preview(service,data)
    result['preview_token']=URLSafeTimedSerializer(current_app.secret_key,salt='sales-import').dumps({'actor':service.actor,'fingerprint':result['fingerprint']}) if result['valid'] else None
    return result

@sales_bp.post('/api/import/commit')
@endpoint(mutation=True)
def import_commit(service,data):
    token=data.pop('preview_token',None)
    try: proof=URLSafeTimedSerializer(current_app.secret_key,salt='sales-import').loads(token,max_age=3600)
    except (BadSignature,TypeError): raise v.SalesError('Preview expired; preview the file again.') from None
    if proof!={'actor':service.actor,'fingerprint':data.get('preview_fingerprint')}: raise v.SalesError('Preview does not match this import.')
    return imports.commit_csv(service,data)

@sales_bp.get('/api/export.csv')
@endpoint(mutation=True,replay=False)
def export(service):
    return Response(imports.export_csv(service,request.args),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="sales-opportunities.csv"'})


def register_sales_routes(app): app.register_blueprint(sales_bp)
