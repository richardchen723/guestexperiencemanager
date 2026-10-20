import base64
from datetime import datetime, timezone

import pytest

from dashboard.inquiries.email_adapter import message_hash, normalize_message


def message():
    return {'id':'gmail-123','threadId':'thread-123','internalDate':'1792515600000','labelIds':['INBOX'],
            'payload':{'mimeType':'multipart/alternative','headers':[{'name':'From','value':'sender@example.invalid'},
                        {'name':'Subject','value':'New stay inquiry EH-12345678'},{'name':'Date','value':'wrong sender timestamp'},
                        {'name':'Message-ID','value':'<original@example.invalid>'}],
                       'parts':[{'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'Name: Sally\nOriginal inquiry\n').decode()}},
                                {'mimeType':'text/html','body':{'data':base64.urlsafe_b64encode(b'<p>Duplicate HTML</p>').decode()}}]}}


def test_gmail_api_uses_receipt_metadata_and_prefers_original_plain_text():
    raw=message()
    normalized=normalize_message(raw,source='enchanted-havens-email',mailbox='larry@enchantedhavens.com')
    assert normalized['body']=='Name: Sally\nOriginal inquiry\n'
    assert normalized['received_at']==datetime.fromtimestamp(1792515600,timezone.utc).isoformat()
    assert normalized['internet_message_id']=='<original@example.invalid>'
    assert len(message_hash(normalized))==64


def test_connector_mime_representation_and_reply_headers_supported():
    raw={'id':'m1','thread_id':'t1','internal_date':'1792515600000','label_ids':['INBOX'],
         'payload':{'mime_type':'text/plain','headers':[{'name':'From','value':'guest@example.invalid'},
                     {'name':'Subject','value':'Re: inquiry'},{'name':'In-Reply-To','value':'<original@example.invalid>'}],
                    'body':{'content':'Original guest reply'}}}
    normalized=normalize_message(raw,source='enchanted-havens-email',mailbox='larry@enchantedhavens.com')
    assert normalized['thread_id']=='t1' and normalized['body']=='Original guest reply'
    assert normalized['in_reply_to']=='<original@example.invalid>'


def test_sent_drafts_spam_and_trash_not_ingested():
    for label in ('SENT','DRAFT','SPAM','TRASH'):
        raw=message();raw['labelIds']=[label]
        assert normalize_message(raw,source='s',mailbox='m') is None


def test_html_only_mail_is_text_and_does_not_execute_or_fetch():
    raw=message();raw['payload']['parts']=[{'mime_type':'text/html','body':{'content':'<div>Name: Sally</div><script>steal()</script><img src="https://tracker.invalid">'}}]
    normalized=normalize_message(raw,source='s',mailbox='m')
    assert 'Name: Sally' in normalized['body']
    assert 'steal()' not in normalized['body'] and 'tracker.invalid' not in normalized['body']


def test_receipt_timestamp_required_and_attachments_ignored():
    raw=message();del raw['internalDate']
    with pytest.raises(ValueError,match='receipt timestamp'):
        normalize_message(raw,source='s',mailbox='m')
    raw=message();raw['payload']['parts'].append({'mimeType':'text/plain','filename':'private.txt','body':{'content':'Not part of inquiry'}})
    assert 'Not part' not in normalize_message(raw,source='s',mailbox='m')['body']
