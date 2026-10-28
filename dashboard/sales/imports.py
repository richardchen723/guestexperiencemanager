"""Bounded CSV preview/commit and replay-safe agent intake. No network calls."""
import csv
import io
import json
from sqlalchemy import select
from . import validation as v
from .models import Intake, Deal

COLUMNS = ('title','owner_name','contact_name','email','phone','property_name','address','market','source_reference')


def parse_csv(content):
    content = v.string(content, 'CSV', 500000, True)
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or set(reader.fieldnames) - set(COLUMNS) or 'title' not in reader.fieldnames:
        raise v.SalesError('CSV needs a title column and only the supported columns: '+', '.join(COLUMNS))
    rows = []
    for number, row in enumerate(reader, 2):
        if number > 201: raise v.SalesError('Import at most 200 rows at a time.')
        if None in row: raise v.SalesError(f'Row {number} has extra values.')
        if not any(row.values()): continue
        data = {'title':row.get('title'), 'source_reference':row.get('source_reference')}
        if row.get('owner_name'): data['account'] = {'name':row['owner_name']}
        if any(row.get(k) for k in ('contact_name','email','phone')):
            data['contacts'] = [{'name':row.get('contact_name'), 'email':row.get('email'), 'phone':row.get('phone')}]
        if any(row.get(k) for k in ('property_name','address','market')):
            data['properties'] = [{'name':row.get('property_name'), 'address':row.get('address'), 'market':row.get('market')}]
        rows.append((number, data))
    if not rows: raise v.SalesError('The CSV contains no data rows.')
    return rows


def preview(service, data):
    v.fields(data, {'csv','source_id'})
    source_id = service.source({'source_id':v.integer(data.get('source_id'),'Source')}).id
    rows = []
    for number, values in parse_csv(data.get('csv')):
        nested = service.db.begin_nested()
        try:
            result = service.create({**values,'source_id':source_id})
            matches = service.deal_duplicates(result['id'])
            rows.append({'row':number, 'title':result['title'], 'valid':True, 'possible_duplicates':matches})
        except v.SalesError as exc:
            rows.append({'row':number, 'title':values.get('title'), 'valid':False, 'error':str(exc)})
        finally:
            nested.rollback()
    return {'rows':rows,'valid':all(row['valid'] for row in rows),'count':len(rows),
            'fingerprint':v.fingerprint(data),'warning':'Each row creates an opportunity. Duplicate suggestions require human review; no automatic merging.'}


def ingest(service, data):
    v.fields(data, {'source_id','external_id','original','lead'})
    source_id = v.integer(data.get('source_id'),'Source')
    service.source({'source_id':source_id})
    external_id = v.string(data.get('external_id'),'External source ID',240,True)
    v.fields(data.get('lead'), {'title','account','account_id','contacts','properties','source_reference','summary','assigned_user_id'})
    original = v.string(data.get('original'),'Original source text',50000,True)
    digest = v.fingerprint(data)
    service.lock_key(f'sales-intake:{source_id}:{external_id}')
    row = service.db.scalar(select(Intake).where(Intake.source_id == source_id,Intake.external_id == external_id))
    if row:
        if row.fingerprint != digest: raise v.SalesError('This source ID already contains different data; review the existing intake record.',409)
        return {'intake_id':row.id,'deal_id':row.deal_id,'status':row.status,'error':row.error,'replayed':True}
    row = Intake(source_id=source_id,external_id=external_id,original=data,fingerprint=digest)
    service.db.add(row)
    service.db.flush()
    nested = service.db.begin_nested()
    try:
        result = service.create({**data['lead'],'source_id':source_id})
        nested.commit()
        row.deal_id, row.status = result['id'], 'processed'
    except v.SalesError as exc:
        nested.rollback()
        row.status, row.error = 'failed', str(exc)
    service.db.flush()
    return {'intake_id':row.id,'deal_id':row.deal_id,'status':row.status,'error':row.error}


def retry(service, identity, data):
    service.require_manager()
    v.fields(data, {'lead'})
    row = service.row(Intake,identity,lock=True)
    if row.status != 'failed': raise v.SalesError('Only failed intake records can be retried.')
    v.fields(data.get('lead'), {'title','account','account_id','contacts','properties','source_reference','summary','assigned_user_id'})
    result = service.create({**data['lead'],'source_id':row.source_id})
    row.status,row.error,row.deal_id = 'processed',None,result['id']
    service.log(service.deal(result['id']),'intake_retry','Reviewed failed intake',{'intake_id':row.id,'correction':data['lead']})
    return result


def commit_csv(service, data):
    v.fields(data, {'csv','source_id','preview_fingerprint','duplicates_reviewed'})
    values = {k:data.get(k) for k in ('csv','source_id')}
    if data.get('preview_fingerprint') != v.fingerprint(values): raise v.SalesError('Preview this exact file and source before importing.')
    if data.get('duplicates_reviewed') is not True: raise v.SalesError('Review the possible duplicates before committing the import.')
    results = []
    for number, lead in parse_csv(data.get('csv')):
        result = ingest(service, {'source_id':data['source_id'],'external_id':f"csv:{data['preview_fingerprint']}:{number}",'original':json.dumps(lead,ensure_ascii=False),'lead':lead})
        if result['status'] != 'processed': raise v.SalesError(f"Row {number}: {result['error']}")
        results.append(result)
    return {'count':len(results),'records':results}


def export_csv(service, filters):
    if not service.can_export: raise v.SalesError('Sales export access is required.',403)
    rows = service.db.scalars(service.filtered(filters).order_by(Deal.id).limit(10001)).all()
    if len(rows)>10000: raise v.SalesError('Narrow the filters to export at most 10,000 opportunities.')
    output=io.StringIO(newline=''); writer=csv.writer(output)
    keys=('code','title','account_name','stage_label','outcome','assigned_name','source_name','property_count','annual_management_revenue','created_at')
    writer.writerow(keys)
    for values in service.summaries(rows):
        # Neutralize spreadsheet formulas, including leading whitespace/control chars.
        writer.writerow([safe_cell(values.get(key)) for key in keys])
    service.log(None,'export',f'Exported {len(rows)} Sales opportunities',{'filters':dict(filters)})
    return output.getvalue()


def safe_cell(value):
    value='' if value is None else str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')) else value
