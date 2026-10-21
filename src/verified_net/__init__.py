"""Official Python client for the Verified.net API (https://verified.net/docs).

    from verified_net import Client
    client = Client()                       # reads VERIFIED_KEY from the environment
    result = client.email("jane@example.com")
    if result["recommendation"]["action"] == "reject":
        ...

No dependencies: uses the standard library only.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Iterable, Optional

__version__ = "1.0.0"
__all__ = ["Client", "VerifiedError", "__version__"]

DEFAULT_BASE_URL = "https://api.verified.net"


class VerifiedError(Exception):
    """An error answer from the API (or a network failure, with status 0).

    code is the API's error code, for example "insufficient_credits", "rate_limited",
    "unauthorized" or "network_error". retry_after is set on 429 answers.
    """

    def __init__(self, status: int, code: str, message: str, request_id: Optional[str] = None,
                 retry_after: Optional[float] = None):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after


class Client:
    """Verified.net API client.

    api_key: your key (default: the VERIFIED_KEY environment variable).
    timeout: seconds per request. max_retries: retries after 429 or a network error.
    """

    def __init__(self, api_key: Optional[str] = None, *, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 15.0, max_retries: int = 2):
        self.api_key = api_key or os.environ.get("VERIFIED_KEY", "")
        if not self.api_key:
            raise ValueError("No API key: pass api_key= or set VERIFIED_KEY.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # -- lookups ---------------------------------------------------------------------------
    def email(self, address: str, *, bypass_cache: bool = False) -> dict:
        """Verify an email address (POST /v1/email)."""
        return self._post("/v1/email", {"address": address, "bypass_cache": bypass_cache})

    def phone(self, number: str, *, default_country: Optional[str] = None, bypass_cache: bool = False) -> dict:
        """Validate a phone number (POST /v1/phone). default_country for numbers without +country code."""
        return self._post("/v1/phone", _clean({"number": number, "default_country": default_country,
                                               "bypass_cache": bypass_cache}))

    def carrier(self, number: str, *, default_country: Optional[str] = None, bypass_cache: bool = False) -> dict:
        """Current carrier and porting status of a US/Canada number (POST /v1/carrier)."""
        return self._post("/v1/carrier", _clean({"number": number, "default_country": default_country,
                                                 "bypass_cache": bypass_cache}))

    def ip(self, ip: str, *, bypass_cache: bool = False) -> dict:
        """Location, network and risk of an IP address (POST /v1/ip)."""
        return self._post("/v1/ip", {"ip": ip, "bypass_cache": bypass_cache})

    def contact(self, email: str, *, name: Optional[str] = None, phone: Optional[str] = None,
                default_country: Optional[str] = None) -> dict:
        """Do a name and/or phone match the records for this email? (POST /v1/contact)."""
        return self._post("/v1/contact", _clean({"email": email, "name": name, "phone": phone,
                                                 "default_country": default_country}))

    def reverse_phone(self, phone: str, *, tier: str = "standard", default_country: Optional[str] = None,
                      bypass_cache: bool = False) -> dict:
        """Who is behind a US/Canada number (POST /v1/reverse/phone). tier: standard or premium."""
        return self._post("/v1/reverse/phone", _clean({"phone": phone, "tier": tier,
                                                       "default_country": default_country,
                                                       "bypass_cache": bypass_cache}))

    def reverse_email(self, email: str, *, bypass_cache: bool = False) -> dict:
        """Who is behind an email address (POST /v1/reverse/email)."""
        return self._post("/v1/reverse/email", {"email": email, "bypass_cache": bypass_cache})

    def account(self) -> dict:
        """Balance, usage and the key's details (GET /v1/account, free)."""
        return self._request("GET", "/v1/account")

    # -- bulk jobs ---------------------------------------------------------------------------
    def create_bulk_job(self, type: str, items: Iterable[str], *, default_country: Optional[str] = None,
                        tier: Optional[str] = None, webhook_url: Optional[str] = None,
                        idempotency_key: Optional[str] = None) -> dict:
        """Start a bulk job from a list of values (POST /v1/bulk/jobs).

        type: email, phone, ip, carrier, reverse_phone or reverse_email. A retry with the same
        idempotency_key within 24 hours returns the same job instead of creating another one;
        one is generated if you don't pass it, so the client's own retries are safe.
        """
        body = _clean({"type": type, "items": list(items), "default_country": default_country, "tier": tier,
                       "webhook_url": webhook_url})
        headers = {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}
        return self._request("POST", "/v1/bulk/jobs", body, headers=headers)

    def bulk_jobs(self) -> list:
        """Your recent bulk jobs (GET /v1/bulk/jobs)."""
        return self._request("GET", "/v1/bulk/jobs")["jobs"]

    def bulk_job(self, job_id: str) -> dict:
        """A job's status and progress (GET /v1/bulk/jobs/{id})."""
        return self._request("GET", f"/v1/bulk/jobs/{_q(job_id)}")

    def cancel_bulk_job(self, job_id: str) -> dict:
        """Cancel a queued or running job; values not yet checked aren't charged."""
        return self._request("DELETE", f"/v1/bulk/jobs/{_q(job_id)}")

    def bulk_results(self, job_id: str):
        """Yield every result item of a job, fetching page after page (JSON format)."""
        page = 1
        while True:
            data = self._request("GET", f"/v1/bulk/jobs/{_q(job_id)}/results?page={page}")
            yield from data["items"]
            if not data.get("has_next"):
                return
            page += 1

    def bulk_results_csv(self, job_id: str) -> bytes:
        """The uploaded rows plus result columns, as CSV bytes."""
        return self._request("GET", f"/v1/bulk/jobs/{_q(job_id)}/results?format=csv", raw=True)

    def wait_for_bulk_job(self, job_id: str, *, poll_seconds: float = 5.0, timeout: Optional[float] = None) -> dict:
        """Poll until the job is completed or cancelled; returns the final job."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            job = self.bulk_job(job_id)
            if job["status"] in ("completed", "cancelled"):
                return job
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"bulk job {job_id} still {job['status']} after {timeout} s")
            time.sleep(poll_seconds)

    # -- transport -----------------------------------------------------------------------------
    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: Any = None, *, headers: Optional[dict] = None,
                 raw: bool = False):
        data = None if body is None else json.dumps(body).encode()
        hdrs = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json",
                "User-Agent": f"verified-net-python/{__version__}"}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        attempt = 0
        while True:
            req = urllib.request.Request(self.base_url + path, data=data, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = resp.read()
                    return payload if raw else json.loads(payload or b"{}")
            except urllib.error.HTTPError as e:
                err = _error_from(e)
                if e.code == 429 and attempt < self.max_retries:
                    attempt += 1
                    time.sleep(min(err.retry_after or 2 ** attempt, 30))
                    continue
                raise err from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < self.max_retries and method == "GET":
                    attempt += 1
                    time.sleep(2 ** attempt)
                    continue
                if attempt < self.max_retries and "Idempotency-Key" in hdrs:
                    attempt += 1                         # safe: the server replays the same job
                    time.sleep(2 ** attempt)
                    continue
                raise VerifiedError(0, "network_error", str(getattr(e, "reason", e))) from None


def _error_from(e: urllib.error.HTTPError) -> VerifiedError:
    try:
        body = json.loads(e.read() or b"{}")
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    retry = e.headers.get("Retry-After") if e.headers else None
    try:
        retry_after = float(retry) if retry else None
    except ValueError:
        retry_after = None
    return VerifiedError(e.code, err.get("code") or "http_error", err.get("message") or e.reason or "",
                         err.get("request_id") or (e.headers.get("X-Request-Id") if e.headers else None),
                         retry_after)


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v is not False}


def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")
