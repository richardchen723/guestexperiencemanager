#!/usr/bin/env python3
"""Read-only PriceLabs connector for Brain booking-health context."""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)


class PriceLabsClient:
    """Small read-only client with graceful degradation when API details vary."""

    def __init__(self):
        self.api_key = os.getenv("PRICELABS_API_KEY")
        self.account_id = os.getenv("PRICELABS_ACCOUNT_ID")
        self.base_url = (os.getenv("PRICELABS_BASE_URL") or "https://api.pricelabs.co/v1").rstrip("/")
        self.pms_name = os.getenv("PRICELABS_PMS_NAME", "hostaway")
        self.timeout = int(os.getenv("PRICELABS_TIMEOUT_SECONDS", "300"))
        self.price_window_days = int(os.getenv("PRICELABS_PRICE_WINDOW_DAYS", "365"))
        self.include_price_reason = os.getenv("PRICELABS_INCLUDE_PRICE_REASON", "true").lower() not in {"0", "false", "no"}
        self.fetch_metrics = os.getenv("PRICELABS_FETCH_METRICS", "true").lower() not in {"0", "false", "no"}
        self.max_retries = max(1, int(os.getenv("PRICELABS_MAX_RETRIES", "4")))
        self.retry_backoff_seconds = max(1.0, float(os.getenv("PRICELABS_RETRY_BACKOFF_SECONDS", "5")))

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.pms_name)

    def fetch_listing_snapshot(self, external_listing_id: str | int) -> dict[str, Any]:
        """
        Fetch a read-only pricing, rules, and booking-pattern snapshot.

        PriceLabs customer API access can differ by account. The connector keeps
        the endpoint paths centralized so the rollout can adjust them without
        touching Brain's signal code.
        """
        if not self.is_configured:
            return {
                "status": "not_configured",
                "confidence": 0.0,
                "payload": None,
                "error": "PriceLabs API key, base URL, or PMS name is not configured",
            }

        prices = self.fetch_listing_prices(external_listing_id)
        metrics = self.fetch_listing_metrics(external_listing_id) if self.fetch_metrics else {
            "status": "skipped",
            "confidence": 0.0,
            "payload": None,
            "error": "PriceLabs listing metrics fetch is disabled",
        }
        payload = {
            "prices": prices.get("payload"),
            "metrics": metrics.get("payload"),
            "requests": {
                "prices": prices.get("request"),
                "metrics": metrics.get("request"),
            },
            "source_statuses": {
                "prices": prices.get("status"),
                "metrics": metrics.get("status"),
            },
        }
        if prices["status"] == "ok":
            confidence = 0.95 if metrics.get("status") == "ok" else 0.85
            return {
                "status": "ok",
                "confidence": confidence,
                "payload": payload,
                "error": metrics.get("error") if metrics.get("status") not in {"ok", "skipped"} else None,
            }
        if metrics.get("status") == "ok":
            return {
                "status": "partial",
                "confidence": 0.55,
                "payload": payload,
                "error": prices.get("error"),
            }
        return {
            "status": prices.get("status") or metrics.get("status") or "unavailable",
            "confidence": 0.1,
            "payload": payload,
            "error": prices.get("error") or metrics.get("error") or "PriceLabs returned no usable pricing or metrics data",
        }

    def fetch_listing_prices(self, external_listing_id: str | int) -> dict[str, Any]:
        """Fetch PriceLabs daily prices and pricing reason data for a listing."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
        if self.account_id:
            headers["X-Account-ID"] = self.account_id

        start = date.today()
        end = start + timedelta(days=max(self.price_window_days, 1))
        payload = {
            "listings": [
                {
                    "id": str(external_listing_id),
                    "pms": self.pms_name,
                    "dateFrom": start.isoformat(),
                    "dateTo": end.isoformat(),
                    "reason": self.include_price_reason,
                }
            ]
        }
        try:
            response = self._request(
                "post",
                f"{self.base_url}/listing_prices",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("PriceLabs listing_prices fetch failed for %s: %s", external_listing_id, exc)
            return {
                "status": "unavailable",
                "confidence": 0.1,
                "payload": {"request": payload},
                "request": payload,
                "error": str(exc),
            }

        listing_payload = data[0] if isinstance(data, list) and data else data
        if isinstance(listing_payload, dict) and listing_payload.get("error"):
            return {
                "status": listing_payload.get("error_status") or "error",
                "confidence": 0.25,
                "payload": listing_payload,
                "request": payload,
                "error": listing_payload.get("error"),
            }
        if listing_payload:
            return {
                "status": "ok",
                "confidence": 0.85,
                "payload": listing_payload,
                "request": payload,
                "error": None,
            }
        return {
            "status": "unavailable",
            "confidence": 0.1,
            "payload": data,
            "request": payload,
            "error": "PriceLabs returned an empty response",
        }

    def fetch_listing_metrics(self, external_listing_id: str | int) -> dict[str, Any]:
        """Fetch PriceLabs booking-pattern metrics for a listing."""
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
        if self.account_id:
            headers["X-Account-ID"] = self.account_id

        params = {
            "listing_id": str(external_listing_id),
            "pms_name": self.pms_name,
        }
        try:
            response = self._request(
                "get",
                f"{self.base_url}/listing_metrics",
                headers=headers,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("PriceLabs listing_metrics fetch failed for %s: %s", external_listing_id, exc)
            return {
                "status": "unavailable",
                "confidence": 0.1,
                "payload": None,
                "request": params,
                "error": str(exc),
            }

        if isinstance(data, dict) and data.get("error"):
            return {
                "status": data.get("error_status") or "error",
                "confidence": 0.25,
                "payload": data,
                "request": params,
                "error": data.get("error"),
            }
        return {
            "status": "ok" if data else "unavailable",
            "confidence": 0.85 if data else 0.1,
            "payload": data,
            "request": params,
            "error": None if data else "PriceLabs returned an empty metrics response",
        }

    def _request(self, method: str, url: str, **kwargs):
        """Retry rate-limited and transient PriceLabs reads with bounded backoff."""
        last_response = None
        for attempt in range(self.max_retries):
            request_fn = requests.post if method == "post" else requests.get
            response = request_fn(url, **kwargs)
            last_response = response
            status_code = int(getattr(response, "status_code", 200) or 200)
            retryable = status_code == 429 or status_code >= 500
            if not retryable or attempt >= self.max_retries - 1:
                return response

            headers = getattr(response, "headers", {}) or {}
            try:
                retry_after = float(headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                retry_after = 0
            wait_seconds = retry_after or min(self.retry_backoff_seconds * (2 ** attempt), 30.0)
            logger.warning(
                "PriceLabs returned HTTP %s; retrying in %.1fs (%s/%s)",
                status_code,
                wait_seconds,
                attempt + 1,
                self.max_retries,
            )
            time.sleep(wait_seconds)
        return last_response
