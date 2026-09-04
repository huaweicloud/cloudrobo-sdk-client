import hashlib
import hmac
import binascii
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse, quote, unquote

logger = logging.getLogger(__name__)

DateFormat = "%Y%m%dT%H%M%SZ"
HXDate = "X-Sdk-Date"
HHost = "host"
HAuthorization = "Authorization"
UTF8 = "utf-8"
SDK_HMAC_SHA256 = "SDK-HMAC-SHA256"


def _urlencode(s):
    return quote(s, safe='~')


class ApigSdkSigner:
    ALGORITHM = SDK_HMAC_SHA256

    def __init__(self, ak: str, sk: str):
        self.ak = ak
        self.sk = sk

    def sign(self, method: str, url: str, headers: dict, body: bytes = b"") -> dict:
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.port and parsed.port not in (80, 443):
            host = f"{host}:{parsed.port}"

        now = datetime.now(timezone.utc)
        sdk_date = now.strftime(DateFormat)

        if HXDate not in {k.lower() for k in headers}:
            headers[HXDate] = sdk_date
        if HHost not in {k.lower() for k in headers}:
            headers[HHost] = host
        if body:
            headers["content-type"] = headers.get("content-type", "application/json")

        path = parsed.path if parsed.path else "/"
        query = parsed.query

        canonical_request = self._build_canonical_request(
            method, path, query, headers, body
        )
        string_to_sign = self._build_string_to_sign(canonical_request, now)
        signature = self._calc_signature(string_to_sign)

        logger.debug("APIG sign: method=%s url=%s", method, url)
        logger.debug("APIG canonical_request:\n%s", canonical_request)
        logger.debug("APIG string_to_sign:\n%s", string_to_sign)
        logger.debug("APIG signature: %s", signature)

        signed_header_names = self._signed_headers(headers)
        headers[HAuthorization] = (
            f"{self.ALGORITHM} Access={self.ak}, "
            f"SignedHeaders={';'.join(signed_header_names)}, "
            f"Signature={signature}"
        )
        return headers

    def _build_canonical_request(self, method, path, query, headers, body):
        canonical_method = method.upper()
        canonical_uri = self._canonical_uri(path)
        canonical_querystring = self._canonical_querystring(query)
        signed_header_names = self._signed_headers(headers)
        canonical_headers = self._canonical_headers(headers, signed_header_names)
        hex_hash = hashlib.sha256(body).hexdigest()
        return "\n".join([
            canonical_method,
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            ";".join(signed_header_names),
            hex_hash,
        ])

    def _build_string_to_sign(self, canonical_request, time):
        hex_hash = hashlib.sha256(canonical_request.encode(UTF8)).hexdigest()
        return f"{self.ALGORITHM}\n{datetime.strftime(time, DateFormat)}\n{hex_hash}"

    def _calc_signature(self, string_to_sign):
        key = self.sk.encode(UTF8)
        msg = string_to_sign.encode(UTF8)
        return binascii.hexlify(
            hmac.new(key, msg, digestmod=hashlib.sha256).digest()
        ).decode()

    @staticmethod
    def _canonical_uri(uri):
        segments = unquote(uri).split("/")
        encoded = [_urlencode(seg) for seg in segments]
        url_path = "/".join(encoded)
        if not url_path.endswith("/"):
            url_path += "/"
        return url_path

    @staticmethod
    def _canonical_querystring(query):
        if not query:
            return ""
        from urllib.parse import parse_qsl
        params = parse_qsl(query, keep_blank_values=True)
        params.sort(key=lambda x: x[0])
        return "&".join(f"{_urlencode(k)}={_urlencode(v)}" for k, v in params)

    @staticmethod
    def _signed_headers(headers):
        arr = []
        for k in headers:
            arr.append(k.lower())
        arr.sort()
        return arr

    @staticmethod
    def _canonical_headers(headers, signed_header_names):
        _headers = {}
        for k in headers:
            key_lower = k.lower()
            value = headers[k]
            if value is None:
                continue
            value_stripped = value.strip()
            _headers[key_lower] = value_stripped
        arr = []
        for k in signed_header_names:
            if k in _headers:
                arr.append(f"{k}:{_headers[k]}")
        return "\n".join(arr) + "\n"
