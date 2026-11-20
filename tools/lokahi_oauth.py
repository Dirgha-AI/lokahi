# -*- coding: utf-8 -*-
"""OAuth 2.1 resource-server support for the LOKAHI HTTP transport.

Implements the subset of the MCP authorization model that a gateway can enforce
on its own:

* RFC 9728 protected-resource metadata at
  ``/.well-known/oauth-protected-resource`` (plus the path-suffixed form for the
  MCP endpoint), so clients can discover the authorization server.
* Bearer JWT validation with a fixed algorithm allowlist, ``kid`` lookup in a
  JWKS, and ``iss`` / ``aud`` / ``exp`` / ``nbf`` checks.
* ``WWW-Authenticate: Bearer resource_metadata="..."`` on 401, as the spec asks.
* No token passthrough: the gateway never forwards the caller's token upstream
  (it does not forward requests at all).

Not implemented here, and stated rather than faked:

* PKCE and authorization-code flows — those belong to the authorization server,
  not the resource server.
* Client ID Metadata Documents and RFC 9207 ``iss`` response validation — those
  are client-side and are R1-02b.
* Token exchange (RFC 8693) for upstream credentials — R1-05.
* Opaque/introspection tokens.

Standard library plus ``cryptography`` for RSA/EC signature verification.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

try:  # pragma: no cover - soft dependency
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    _CRYPTO = True
except ImportError:  # pragma: no cover
    _CRYPTO = False


class OAuthError(RuntimeError):
    """A bearer token was absent, malformed, or failed a check."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# Base64url helpers
# ---------------------------------------------------------------------------

def _b64url_decode(value: str) -> bytes:
    padding_needed = (-len(value)) % 4
    return base64.urlsafe_b64decode(value + "=" * padding_needed)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Protected resource metadata (RFC 9728)
# ---------------------------------------------------------------------------

@dataclass
class ResourceMetadata:
    resource: str                       # canonical URI of this gateway
    authorization_servers: list[str]    # issuer URLs
    bearer_methods_supported: list[str] = field(default_factory=lambda: ["header"])
    scopes_supported: list[str] = field(default_factory=list)
    resource_documentation: str = ""

    def as_dict(self) -> dict[str, Any]:
        doc = {
            "resource": self.resource,
            "authorization_servers": list(self.authorization_servers),
            "bearer_methods_supported": list(self.bearer_methods_supported),
        }
        if self.scopes_supported:
            doc["scopes_supported"] = list(self.scopes_supported)
        if self.resource_documentation:
            doc["resource_documentation"] = self.resource_documentation
        return doc

    def www_authenticate(self) -> str:
        """The challenge a 401 should carry, pointing at the metadata."""
        return (
            f'Bearer resource_metadata="{self.resource.rstrip("/")}'
            '/.well-known/oauth-protected-resource"'
        )


# ---------------------------------------------------------------------------
# JWKS
# ---------------------------------------------------------------------------

class JWKSCache:
    """Fetch and cache a JSON Web Key Set, with a hard failure mode.

    A fetch failure raises rather than falling back to unverified tokens.
    """

    def __init__(self, url: str, ttl_seconds: int = 300, timeout: int = 5) -> None:
        self.url = url
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> dict[str, Any]:
        if not _CRYPTO:
            raise OAuthError("cryptography_missing", "the 'cryptography' package is required")
        request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            raise OAuthError("jwks_unavailable", str(exc)) from exc

        keys: dict[str, Any] = {}
        for jwk in payload.get("keys", []):
            kid = jwk.get("kid")
            if not kid or jwk.get("use") not in (None, "sig"):
                continue
            try:
                if jwk.get("kty") == "RSA":
                    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
                    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
                    keys[kid] = rsa.RSAPublicNumbers(e, n).public_key()
                elif jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
                    x = int.from_bytes(_b64url_decode(jwk["x"]), "big")
                    y = int.from_bytes(_b64url_decode(jwk["y"]), "big")
                    keys[kid] = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
            except (KeyError, ValueError, TypeError):
                continue
        if not keys:
            raise OAuthError("jwks_empty", "no usable signing keys in the key set")
        return keys

    def get(self, kid: str) -> Any:
        with self._lock:
            fresh = (time.time() - self._fetched_at) < self.ttl_seconds and kid in self._keys
            if fresh:
                return self._keys[kid]
            self._keys = self._fetch()
            self._fetched_at = time.time()
            if kid not in self._keys:
                raise OAuthError("unknown_kid", f"kid {kid!r} not present in the key set")
            return self._keys[kid]

    def get_any(self) -> dict[str, Any]:
        with self._lock:
            if (time.time() - self._fetched_at) >= self.ttl_seconds or not self._keys:
                self._keys = self._fetch()
                self._fetched_at = time.time()
            return dict(self._keys)


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

_ALLOWED_ALGS = {"RS256", "RS384", "RS512", "ES256", "HS256"}

_ALG_HASH = {
    "RS256": hashes.SHA256,
    "RS384": hashes.SHA384,
    "RS512": hashes.SHA512,
    "ES256": hashes.SHA256,
}


@dataclass
class Claims:
    subject: str
    issuer: str
    audience: list[str]
    expires_at: float
    raw: dict

    @property
    def client_id(self) -> str:
        return str(self.raw.get("client_id") or self.subject)


class JWTVerifier:
    """Verify a compact JWS bearer token.

    Parameters
    ----------
    issuers: accepted ``iss`` values (exact match).
    audience: required value in ``aud``; normally the protected resource URI.
    algorithms: allowlist. ``none`` is never accepted.
    jwks_url: JWKS location for RS*/ES* tokens.
    hs256_secret: shared secret for HS256 tokens (local testing only).
    leeway_seconds: clock skew allowance for exp/nbf/iat.
    """

    def __init__(
        self,
        *,
        issuers: list[str],
        audience: str,
        algorithms: Optional[list[str]] = None,
        jwks_url: Optional[str] = None,
        hs256_secret: Optional[bytes] = None,
        leeway_seconds: int = 30,
    ) -> None:
        if not issuers:
            raise ValueError("at least one issuer is required")
        if not audience:
            raise ValueError("audience is required")
        self.issuers = list(issuers)
        self.audience = audience
        self.algorithms = [a for a in (algorithms or ["RS256", "ES256"])]
        bad = [a for a in self.algorithms if a not in _ALLOWED_ALGS]
        if bad:
            raise ValueError(f"unsupported algorithm(s): {bad}")
        if "HS256" in self.algorithms and not hs256_secret:
            raise ValueError("HS256 requires hs256_secret")
        self.jwks = JWKSCache(jwks_url) if jwks_url else None
        self.hs256_secret = hs256_secret
        self.leeway = int(leeway_seconds)

    @classmethod
    def from_config(cls, config: dict) -> "JWTVerifier":
        return cls(
            issuers=list(config["issuers"]),
            audience=config["audience"],
            algorithms=list(config.get("algorithms", ["RS256", "ES256"])),
            jwks_url=config.get("jwks_url"),
            hs256_secret=(
                config["hs256_secret"].encode("utf-8")
                if config.get("hs256_secret")
                else None
            ),
            leeway_seconds=int(config.get("leeway_seconds", 30)),
        )

    # ------------------------------------------------------------------
    def verify(self, authorization_header: str) -> Claims:
        if not authorization_header:
            raise OAuthError("missing_token")
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise OAuthError("malformed_authorization")
        return self.verify_token(token.strip())

    def verify_token(self, token: str) -> Claims:
        parts = token.split(".")
        if len(parts) != 3:
            raise OAuthError("malformed_token", "expected header.payload.signature")

        try:
            header = json.loads(_b64url_decode(parts[0]))
            payload = json.loads(_b64url_decode(parts[1]))
        except (ValueError, json.JSONDecodeError) as exc:
            raise OAuthError("malformed_token", str(exc)) from exc

        alg = header.get("alg", "")
        if alg not in self.algorithms:
            raise OAuthError("algorithm_not_allowed", f"alg {alg!r} is not accepted")
        if alg == "none":
            raise OAuthError("algorithm_not_allowed", "unsigned tokens are refused")

        signing_input = (parts[0] + "." + parts[1]).encode("ascii")
        signature = _b64url_decode(parts[2])
        self._verify_signature(alg, header.get("kid"), signing_input, signature)
        return self._verify_claims(payload)

    # ------------------------------------------------------------------
    def _verify_signature(self, alg: str, kid: Optional[str], signing_input: bytes,
                          signature: bytes) -> None:
        if alg == "HS256":
            if not self.hs256_secret:
                raise OAuthError("algorithm_not_allowed", "HS256 is not configured")
            expected = hmac.new(self.hs256_secret, signing_input, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, signature):
                raise OAuthError("bad_signature")
            return

        if not _CRYPTO:
            raise OAuthError("cryptography_missing", "the 'cryptography' package is required")
        if self.jwks is None:
            raise OAuthError("jwks_unavailable", "no JWKS configured for asymmetric tokens")

        key = self.jwks.get(kid) if kid else next(iter(self.jwks.get_any().values()))
        try:
            if alg.startswith("RS"):
                key.verify(signature, signing_input, padding.PKCS1v15(), _ALG_HASH[alg]())
            elif alg == "ES256":
                if len(signature) != 64:
                    raise OAuthError("bad_signature", "ES256 signature must be 64 bytes")
                r = int.from_bytes(signature[:32], "big")
                s = int.from_bytes(signature[32:], "big")
                key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
            else:  # pragma: no cover - guarded by the allowlist
                raise OAuthError("algorithm_not_allowed", alg)
        except InvalidSignature as exc:
            raise OAuthError("bad_signature") from exc
        except OAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OAuthError("bad_signature", type(exc).__name__) from exc

    def _verify_claims(self, payload: dict) -> Claims:
        now = time.time()

        issuer = payload.get("iss")
        if not isinstance(issuer, str) or issuer not in self.issuers:
            raise OAuthError("bad_issuer", f"issuer {issuer!r} is not accepted")

        aud = payload.get("aud")
        audiences = [aud] if isinstance(aud, str) else list(aud or [])
        if self.audience not in audiences:
            raise OAuthError("bad_audience", f"audience {audiences!r} does not include {self.audience!r}")

        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            raise OAuthError("missing_expiry")
        if now > float(exp) + self.leeway:
            raise OAuthError("token_expired")

        nbf = payload.get("nbf")
        if isinstance(nbf, (int, float)) and now + self.leeway < float(nbf):
            raise OAuthError("token_not_yet_valid")

        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OAuthError("missing_subject")

        return Claims(
            subject=subject,
            issuer=issuer,
            audience=audiences,
            expires_at=float(exp),
            raw=payload,
        )
