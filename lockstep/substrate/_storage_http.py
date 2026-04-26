"""Thin httpx client for the TS storage service.

One method per endpoint exposed by ``services/storage-ts/server.ts``.
Status codes and error codes from the service map to exceptions per the
locked error contract documented in the spec and the service's README:

- 200 / 204: success.
- 400 (client bug — malformed input we shouldn't have sent): plain
  ``RuntimeError`` so it propagates without retry. ``_with_retry``
  catches ``SubstrateError`` only.
- 404 (lookup miss in the service's per-process index): ``SubstrateError``
  — adapter retries via ``_with_retry``. The bytes may exist on 0G but
  the index entry is gone (service restart, different uploader).
- 422 (trust violation: sha256 mismatch on upload, downloaded bytes
  don't match expected root, attestation pubkey not authorized):
  ``TrustViolation``. Byzantine evidence — never retry.
- 5xx (SDK / indexer / RPC failure): ``SubstrateError``. Transient.
- Transport errors (connect refused, timeout, etc.): ``SubstrateError``.

This module owns no retry logic. The adapter wraps each call in
``_with_retry`` so the wall-clock budget contract stays single-sided.
"""

from __future__ import annotations

import base64
from types import TracebackType
from typing import Any, NoReturn

import httpx

from lockstep.errors import SubstrateError, TrustViolation
from lockstep.evaluation.canonical import Bytes32Hex

_DEFAULT_TIMEOUT_SECONDS = 60.0


class _StorageHttpClient:
    """HTTP client for the long-lived TS storage service.

    Construction is cheap (no network); the underlying ``httpx.Client``
    lazy-connects on first request and pools connections across calls.
    Use as a context manager (``with _StorageHttpClient(url) as c:``) or
    call ``close()`` explicitly when done. The adapter holds one client
    instance for its lifetime.
    """

    def __init__(
        self,
        service_url: str,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._service_url = service_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> _StorageHttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- endpoints ----

    def healthz(self) -> dict[str, Any]:
        response = self._request("GET", "/healthz")
        return _json(response)

    def upload_encrypted_solution(
        self,
        bundle: bytes,
        *,
        plaintext_commitment: Bytes32Hex,
        recipient_pubkey: Bytes32Hex,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/upload-encrypted-solution",
            content=bundle,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Plaintext-Commitment": plaintext_commitment,
                "X-Recipient-Pubkey": recipient_pubkey,
            },
        )
        return _json(response)

    def download_encrypted_solution(self, uri: str) -> bytes:
        response = self._request(
            "GET",
            "/download-encrypted-solution",
            params={"uri": uri},
        )
        return response.content

    def upload_receipt(self, body: bytes) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/upload-receipt",
            content=body,
            headers={"Content-Type": "application/octet-stream"},
        )
        return _json(response)

    def download_receipt(self, uri: str) -> bytes:
        response = self._request(
            "GET",
            "/download-receipt",
            params={"uri": uri},
        )
        return response.content

    def upload_dataset(
        self,
        *,
        public_root: Bytes32Hex,
        private_root: Bytes32Hex,
        public_payload: bytes,
        private_payload: bytes,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/upload-dataset",
            json={
                "public_root": public_root,
                "private_root": private_root,
                "public_b64": base64.b64encode(public_payload).decode("ascii"),
                "private_b64": base64.b64encode(private_payload).decode("ascii"),
            },
        )
        return _json(response)

    def load_dataset_public(self, public_root: Bytes32Hex) -> bytes:
        response = self._request(
            "GET",
            "/load-dataset-public",
            params={"public_root": public_root},
        )
        return response.content

    def load_dataset_full(
        self,
        *,
        public_root: Bytes32Hex,
        private_root: Bytes32Hex,
        attestation_pubkey: Bytes32Hex,
    ) -> bytes:
        response = self._request(
            "GET",
            "/load-dataset-full",
            params={
                "public_root": public_root,
                "private_root": private_root,
                "attestation_pubkey": attestation_pubkey,
            },
        )
        return response.content

    def authorize_attestation(self, pubkey: Bytes32Hex) -> None:
        self._request(
            "POST",
            "/authorize-attestation",
            json={"pubkey": pubkey},
        )

    # ---- internals ----

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._service_url}{path}"
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise SubstrateError(
                f"transport error talking to TS storage service at {url}: {exc}"
            ) from exc
        if response.status_code < 400:
            return response
        _raise_for_status(response, method=method, url=url)


def _json(response: httpx.Response) -> dict[str, Any]:
    """Parse JSON body from a 2xx response. Returns {} on 204 No Content."""
    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


def _raise_for_status(response: httpx.Response, *, method: str, url: str) -> NoReturn:
    """Map an error response to the right exception per the locked contract."""
    status = response.status_code
    error_code, detail = _parse_error_body(response)
    msg = (
        f"TS storage service {method} {url} returned {status} "
        f"{error_code}: {detail}"
    )
    if status == 422:
        raise TrustViolation(msg)
    if status == 400:
        # Programming bug on our side — RuntimeError propagates without
        # retry (the adapter's _with_retry only catches SubstrateError).
        # The caller's inputs may be fine; the bug is in how this module
        # marshals them.
        raise RuntimeError(msg)
    raise SubstrateError(msg)


def _parse_error_body(response: httpx.Response) -> tuple[str, str]:
    """Pull (error_code, detail) from a service error body, with fallbacks."""
    try:
        body = response.json()
    except (ValueError, httpx.DecodingError):
        return "unknown", response.text or "<empty body>"
    if not isinstance(body, dict):
        return "unknown", str(body)
    error_code = str(body.get("error", "unknown"))
    detail = str(body.get("detail", ""))
    return error_code, detail


__all__ = ["_StorageHttpClient"]
