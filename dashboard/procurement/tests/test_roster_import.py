"""Initial imports must resolve actual properties and never reset manual edits."""

import pytest
from sqlalchemy import select

from dashboard.procurement.import_roster import import_roster, resolve_roster
from dashboard.procurement.models import Staff
from dashboard.procurement.service import WorkflowError
from dashboard.procurement.tests.test_workflow import service


PROPERTIES = {
    1:{'id':1,'name':"Harbor's Edge",'tags':['coast']},
    2:{'id':2,'name':"New: Harbor's Edge",'tags':['coast']},
    3:{'id':3,'name':'Unit A','tags':['city units']},
    4:{'id':4,'name':'Unit B','tags':['city units']},
}


def test_import_resolves_punctuation_aliases_and_whole_groups():
    result = resolve_roster(PROPERTIES,[{'name':'Cleaner One','properties':['Harbor’s Edge',"New: Harbor's Edge"]},
                                       {'name':'Cleaner Two','property_tags':['City Units']}])
    assert result[0]['listing_ids'] == [1,2]
    assert result[1]['listing_ids'] == [3,4]
    assert result[0]['phone'] == ''


@pytest.mark.parametrize('entries',[
    [{'name':'Cleaner','properties':['Missing']}],
    [{'name':'Cleaner','property_tags':['Unknown group']}],
    [{'name':'Cleaner','listing_ids':[True]}],
    [{'name':'Cleaner'},{'name':'CLEANER'}],
    [{'name':'Cleaner','properties':'Unit A'}],
])
def test_unresolved_or_invalid_imports_fail_before_changes(entries):
    with pytest.raises(WorkflowError):
        resolve_roster(PROPERTIES,entries)


def test_import_is_once_only_and_does_not_overwrite_later_manual_changes(service):
    entries = [{'name':'New Test Cleaner','listing_ids':[101]}]
    result = import_roster(service,entries,'qa-initial',apply=True)
    assert len(result['created']) == 1
    staff = service.session.get(Staff,result['created'][0]['id'])
    assert staff.user_id is None and staff.phone == ''
    service.save_staff({'id':staff.id,'version':staff.version,'name':'Renamed Cleaner','listing_ids':[102]})
    replay = import_roster(service,entries,'qa-initial',apply=True)
    assert replay['replayed'] is True
    assert staff.name == 'Renamed Cleaner' and staff.listing_ids == [102]
    assert len(list(service.session.scalars(select(Staff)))) == 4
    with pytest.raises(WorkflowError):
        import_roster(service,[{'name':'Different','listing_ids':[101]}],'qa-initial',apply=True)


def test_preview_and_preexisting_people_are_not_overwritten(service):
    preview = import_roster(service,[{'name':'Preview Cleaner','listing_ids':[101]}],'qa-preview')
    assert len(preview['created']) == 1
    assert service.session.scalar(select(Staff).where(Staff.name == 'Preview Cleaner')) is None
    existing = import_roster(service,[{'name':'Maria','listing_ids':[]}],'qa-existing',apply=True)
    assert existing['created'] == [] and existing['skipped_existing'] == ['Maria']
    assert service.session.get(Staff,1).listing_ids == [101,102]
