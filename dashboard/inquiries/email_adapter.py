"""Convert authorized Gmail/connector reads into the CRM's bounded intake format.

No credentials are taken from browser sessions. This adapter sends no email and
does not mark messages read. Plain source text and the Gmail reference survive.
"""
import base64
import hashlib
import json
from datetime import datetime, timezone
from html.parser import HTMLParser


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'):
            self.hidden += 1
        if tag in ('p','div','br','li','tr','blockquote'):
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script','style'):
            self.hidden = max(0,self.hidden-1)
        if tag in ('p','div','li','tr','blockquote'):
            self.text.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def mime_text(payload):
    plain, html = [], []
    def visit(part):
        if part.get('filename'):
            return # Attachments are not part of the inquiry body.
        kind = part.get('mimeType') or part.get('mime_type')
        body = part.get('body') or {}
        value = body.get('content')
        data = body.get('data') or body.get('base64_url_content')
        if value is None and data:
            value = base64.urlsafe_b64decode(data + '='*(-len(data)%4)).decode('utf-8',errors='replace')
        if value:
            (plain if kind == 'text/plain' else html if kind == 'text/html' else []).append(value)
        for child in part.get('parts') or []:
            visit(child)
    visit(payload)
    if plain:
        return '\n'.join(plain)
    if html:
        parser = PlainHTML();parser.feed('\n'.join(html));return ''.join(parser.text)
    raise ValueError('This message has no supported text body. Review the source in Gmail.')


def normalize_message(message, *, source, mailbox, extraction=None):
    labels = message.get('labelIds') or message.get('label_ids') or []
    if set(labels) & {'SENT','DRAFT','SPAM','TRASH'}:
        return None
    payload = message.get('payload') or {}
    headers = {h['name'].lower():h['value'] for h in payload.get('headers',[])}
    timestamp = message.get('internalDate') or message.get('internal_date')
    if not timestamp:
        raise ValueError('Gmail receipt timestamp is required; do not substitute the sender Date header.')
    result = {'source':source,'mailbox':mailbox,'message_id':message['id'],
              'thread_id':message.get('threadId') or message.get('thread_id'),
              'internet_message_id':headers.get('message-id'),'in_reply_to':headers.get('in-reply-to'),
              'sender':headers.get('from'),'subject':headers.get('subject'), 'body':mime_text(payload),
              'received_at':datetime.fromtimestamp(int(timestamp)/1000,timezone.utc).isoformat()}
    if extraction is not None:
        result['extraction'] = extraction
    return result


def message_hash(message):
    return hashlib.sha256(json.dumps(message,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
