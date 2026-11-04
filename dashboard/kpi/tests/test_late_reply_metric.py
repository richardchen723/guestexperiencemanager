"""The 30-minute inclusive metric shares the existing matched-pair sample."""
from datetime import datetime,timedelta,timezone

from dashboard.kpi.estimates import estimate_response
from dashboard.kpi.operations import build_operations,response
from dashboard.kpi.tests.test_estimates import NOW,W,data,msg


def pair(index,minutes,at=None):
    when=at or NOW-timedelta(days=10)
    return [{'id':index*2,'listing_id':1,'conversation_id':index,'incoming':True,'at':when,'body':'What is the wifi password?'}, {'id':index*2+1,'listing_id':1,'conversation_id':index,'incoming':False,'at':when+timedelta(minutes=minutes),'body':'The wifi password is written on the router.'}]


def test_exact_30_below_above_and_fast_counts_share_denominator_and_dedupe():
    d=data();d['messages']=pair(1,4)+pair(2,30-1/60000)+pair(3,30)+pair(4,30+1/60000)
    d['messages']+=d['messages'][:2]
    d['messages']+=[msg(100,0,True,'Where is the parking access code?')|{'conversation_id':100}]
    r=estimate_response(d,W,NOW); late=r['late_replies_30m']
    assert r['numerator']==1 and r['denominator']==4  # Existing under-five measure unchanged.
    assert late['count']==2 and late['percentage']==50 and late['matched_pairs']==4
    assert late['unmatched_request_candidates']==1 and late['target_count']==0
    assert len(late['records'])==2 and all(x['elapsed_minutes']>=30 for x in late['records'])
    assert {x['thread_reference'] for x in late['records']}=={'3','4'}
    assert all('body' not in x for x in late['records']) and r['goal_status']=='unknown'


def test_missing_timestamps_are_unknown_and_do_not_crash_all_six_metrics():
    d=data();d['allow_estimates']=True
    d['messages']=pair(1,40)
    d['messages'][1]['at']=None
    d['messages']+=[{'id':50,'listing_id':1,'conversation_id':5,'incoming':True,'at':None,'body':'Please help with the wifi password.'}]
    payload=build_operations(d,as_of=NOW)
    r=next(m for m in payload['metrics'] if m['key']=='response');late=r['late_replies_30m']
    assert len(payload['metrics'])==6 and r['value'] is None
    assert late['count'] is None and late['percentage'] is None and late['matched_pairs']==0
    assert late['unmatched_request_candidates']==1 and late['missing_timestamp_messages']==2 and late['unplaced_request_candidates']==1


def test_property_local_boundaries_exclude_outside_pairs_and_do_not_call_unmatched_late():
    start=datetime(2026,7,23,4,tzinfo=timezone.utc)  # New York midnight.
    last=datetime(2026,10,23,3,29,tzinfo=timezone.utc)
    d=data();d['messages']=pair(1,30,start)+pair(2,30,start-timedelta(microseconds=1))+pair(3,30,last)+pair(4,30,last+timedelta(minutes=1))
    r=estimate_response(d,W,NOW);late=r['late_replies_30m']
    assert late['count']==late['matched_pairs']==2
    assert late['unmatched_request_candidates']==1
    assert {x['thread_reference'] for x in late['records']}=={'1','3'}


def test_late_record_list_is_bounded_and_keeps_full_count():
    d=data();d['messages']=[message for i in range(30) for message in pair(i+1,30+i)]
    late=estimate_response(d,W,NOW)['late_replies_30m']
    assert late['count']==late['matched_pairs']==30 and late['percentage']==100
    assert len(late['records'])==late['record_limit']==25
    assert late['records'][0]['elapsed_minutes']==59


def test_verified_response_path_uses_same_inclusive_threshold():
    d=data();d['messages_complete']=True;d['messages']=pair(1,30)
    d['messages'][0].update(needs_reply=True,needs_reply_verified=True,original_timestamp_verified=True)
    d['messages'][1].update(sent=True,answer_kind='meaningful',semantics_verified=True,original_timestamp_verified=True)
    r=response(d,W,NOW)
    assert r['late_replies_30m']['count']==1 and r['late_replies_30m']['percentage']==100
    assert r['late_replies_30m']['basis']=='verified_meaningful_pairs'
    assert r['numerator']==0 and r['denominator']==1
