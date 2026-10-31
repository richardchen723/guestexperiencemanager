"""Worker-only Hostaway reads, without importing staff config or its environment."""
import os
import threading
import time

import requests


class HostawayReadClient:
    base = 'https://api.hostaway.com/v1'

    def __init__(self):
        self.account = os.environ['HOSTAWAY_ACCOUNT_ID']
        self.secret = os.environ['HOSTAWAY_API_KEY']
        self.token = None
        self.expires = 0
        self.lock = threading.Lock()

    def _token(self):
        with self.lock:
            if not self.token or time.monotonic() >= self.expires:
                response = requests.post(self.base+'/accessTokens', data={'grant_type':'client_credentials',
                    'client_id':self.account, 'client_secret':self.secret, 'scope':'general'}, timeout=(10,45))
                response.raise_for_status()
                data = response.json()
                self.token = data['access_token']
                self.expires = time.monotonic()+max(1,int(data.get('expires_in',3600))-60)
                time.sleep(1)  # Hostaway tokens activate asynchronously.
            return self.token

    def _read(self, path, params=None):
        for attempt in range(3):
            response = requests.get(self.base+'/'+path, params=params,
                headers={'Authorization':'Bearer '+self._token()}, timeout=(10,45))
            if response.status_code==401:
                with self.lock:
                    self.token=None
                continue
            if response.status_code==429 or response.status_code>=500:
                time.sleep(2*(attempt+1))
                continue
            response.raise_for_status()
            body=response.json()
            return body.get('result') if isinstance(body,dict) else None
        return None

    def get_reservation_detail(self, reservation_id):
        if type(reservation_id) is not int or reservation_id<=0:
            raise ValueError('An exact positive reservation ID is required.')
        return self._read(f'reservations/{reservation_id}', {'includeResources':1})

    def get_reservation_finance_fields(self, reservation_id):
        if type(reservation_id) is not int or reservation_id<=0:
            raise ValueError('An exact positive reservation ID is required.')
        return self._read(f'financeField/{reservation_id}')

    def get_calendar(self, listing_id, start, end):
        if type(listing_id) is not int or listing_id <= 0:
            raise ValueError('An exact positive listing ID is required.')
        return self._read(f'listings/{listing_id}/calendar', {'startDate':start.isoformat(), 'endDate':end.isoformat()})
