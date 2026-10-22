"""Settle one Foldyn Skill credit on this computer.

The billing service checks the account token, the idempotency key, and the
balance. This script cannot spend a credit the service refuses.

  python scripts/foldyn_credit.py doctor
  python scripts/foldyn_credit.py lock <job-dir>
  python scripts/foldyn_credit.py settle <job-dir>

settle holds one credit first. It confirms that hold only when three checks
pass: the print-design app (doctor), the delivery-package check, and the
customer's locked artwork. If any check fails, it releases the hold.

doctor also calls the billing service's read-only /health endpoint. A
certificate failure names Install Certificates.command. Every billing
request sends User-Agent foldyn-skill/<version>. A dropped connection is
retried once with the same idempotency key.

An open hold lasts 30 minutes. After that the service releases it and
returns the credit. settle then uses the next idempotency key, which can
hold a new credit. Repeating a key that is still held or already confirmed
does not hold a second credit.

Token, first match wins:
  FOLDYN_BILLING_TOKEN
  token in ~/.cache/foldyn/billing.json
URL, first match wins:
  FOLDYN_BILLING_URL
  url in that same file
  https://billing.foldyn.co.uk

The token is not written into the job directory.
"""
import http.client
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://billing.foldyn.co.uk"
# Sent on every billing request. urllib's default Python-urllib UA is
# rejected by Cloudflare error 1010 in front of the billing host.
CLIENT_VERSION = "1.1.0"
USER_AGENT = f"foldyn-skill/{CLIENT_VERSION}"
# One retry after the first attempt. The body, including the idempotency key, stays the same.
_NETWORK_TRIES = 2
CERT_MESSAGE = (
    "billing API certificate could not be verified (CERTIFICATE_VERIFY_FAILED). "
    "The python.org macOS installer does not include root certificates until you run "
    "Install Certificates.command in the Python folder under /Applications "
    "(for example /Applications/Python 3.11/Install Certificates.command). "
    "Or install certifi (pip install certifi); this client uses it automatically "
    "when SSL_CERT_FILE is not set. You can also set SSL_CERT_FILE to the path from "
    'python -c "import certifi; print(certifi.where())".'
)
APP_PATH = "/Applications/Adobe Illustrator 2026"
RECORD_NAME = "skill_credit.json"
LOCK_NAME = "artwork_locked.json"
PACKAGE_NAME = "release_check.json"
VERDICT_OK = "可交付"


class SkillCreditError(Exception):
    pass


def illustrator_ready(app_path=APP_PATH, runner=None, platform=None):
    """Hard gate: the print-design app must be on this Mac and answer."""
    plat = sys.platform if platform is None else platform
    if plat != "darwin":
        return False
    if not os.path.exists(app_path):
        return False
    run = _osascript_version if runner is None else runner
    try:
        return bool(run())
    except Exception:
        return False


def _osascript_version():
    import subprocess

    completed = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application id "com.adobe.illustrator" to do javascript "app.version"',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def doctor(app_path=APP_PATH, runner=None, platform=None, probe=None, base_url=None):
    """App gate plus one read-only call to the billing service.

    probe replaces the health call in tests. It raises SkillCreditError on failure.
    """
    app_ok = illustrator_ready(app_path=app_path, runner=runner, platform=platform)
    lines = []
    if app_ok:
        lines.append("OK   print-design app")
    else:
        lines.append(
            "MISS print-design app   -> install Adobe Illustrator on this Mac and allow automation"
        )
    billing_ok = True
    try:
        if probe is None:
            billing_reachable(base_url)
        else:
            probe()
    except SkillCreditError as exc:
        billing_ok = False
        lines.append(f"MISS billing service   -> {exc}")
    else:
        lines.append("OK   billing service")
    lines.append("DOCTOR PASS" if app_ok and billing_ok else "DOCTOR FAIL")
    print("\n".join(lines))
    return app_ok and billing_ok


def package_ok(job_dir):
    path = os.path.join(job_dir, "work", PACKAGE_NAME)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("verdict") == VERDICT_OK


def artwork_ok(job_dir):
    path = os.path.join(job_dir, "work", LOCK_NAME)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("locked") is True


def lock_artwork(job_dir, at=None):
    """Record that the customer locked the artwork. This does not spend a credit."""
    import datetime

    path = os.path.join(job_dir, "work", LOCK_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stamp = at or datetime.datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"locked": True, "at": stamp}, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    return path


def _config_file(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_billing(environ=None, config_path=None):
    env = os.environ if environ is None else environ
    path = config_path or os.path.join(
        os.path.expanduser("~/.cache/foldyn"), "billing.json"
    )
    file_data = _config_file(path)
    token = (env.get("FOLDYN_BILLING_TOKEN") or "").strip()
    url = (env.get("FOLDYN_BILLING_URL") or "").strip()
    if not token:
        token = str(file_data.get("token") or "").strip()
    if not url:
        url = str(file_data.get("url") or "").strip()
    if not token:
        raise SkillCreditError("FOLDYN_BILLING_TOKEN is not set")
    return _check_base(url or DEFAULT_URL), token


def billing_base(environ=None, config_path=None):
    """Billing origin for the health check. A token is not required."""
    env = os.environ if environ is None else environ
    path = config_path or os.path.join(
        os.path.expanduser("~/.cache/foldyn"), "billing.json"
    )
    file_data = _config_file(path)
    url = (env.get("FOLDYN_BILLING_URL") or "").strip()
    if not url:
        url = str(file_data.get("url") or "").strip()
    return _check_base(url or DEFAULT_URL)


def billing_reachable(base_url=None, environ=None, config_path=None, get=None):
    """GET /health. Read-only: it does not send the account token."""
    base = _check_base(base_url) if base_url else billing_base(environ, config_path)
    fetch = get or _urllib_get
    status, body = fetch(base + "/health")
    if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        raise SkillCreditError(f"billing service health check failed (HTTP {status})")
    return True


def _check_base(url):
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme == "https" and host:
        return url.rstrip("/")
    if parsed.scheme == "http" and host in ("127.0.0.1", "localhost"):
        return url.rstrip("/")
    raise SkillCreditError("FOLDYN_BILLING_URL must be https")


class BillingClient:
    """POST JSON. The token is not included in errors."""

    def __init__(self, base_url, token, post=None):
        self.base_url = _check_base(base_url)
        if not token:
            raise SkillCreditError("FOLDYN_BILLING_TOKEN is not set")
        self.token = token
        self._post = post or _urllib_post

    def call(self, path, payload):
        status, body = self._post(self.base_url + path, payload, self.token)
        if not isinstance(body, dict):
            raise SkillCreditError(f"billing API returned HTTP {status}")
        if status >= 400:
            message = body.get("error")
            if not isinstance(message, str) or not message or self.token in message:
                message = f"billing API returned HTTP {status}"
            raise SkillCreditError(message)
        return body


class _Dropped(Exception):
    def __init__(self, cause):
        super().__init__("dropped")
        self.cause = cause


def _is_certificate_error(exc):
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        text = str(current)
        if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text.lower():
            return True
        nxt = getattr(current, "reason", None)
        if nxt is None or nxt is current:
            nxt = getattr(current, "__cause__", None)
        current = nxt if nxt is not current else None
    return False


def _ssl_context():
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _urllib_once(url, payload, token, method):
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, http.client.HTTPException, OSError) as exc:
        if _is_certificate_error(exc):
            raise SkillCreditError(CERT_MESSAGE) from exc
        raise _Dropped(exc) from exc
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        body = {}
    return status, body


def _urllib_exchange(url, payload, token, method):
    dropped = None
    for _attempt in range(_NETWORK_TRIES):
        try:
            return _urllib_once(url, payload, token, method)
        except _Dropped as exc:
            dropped = exc
    raise SkillCreditError("billing API could not be reached") from dropped.cause


def _urllib_post(url, payload, token):
    return _urllib_exchange(url, payload, token, "POST")


def _urllib_get(url):
    return _urllib_exchange(url, None, None, "GET")


def _job_meta(job_dir):
    path = os.path.join(job_dir, "job.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"slug": os.path.basename(os.path.abspath(job_dir)) or "job", "version": "R1"}


def idempotency_key(meta, attempt):
    slug = str(meta.get("slug") or "job")
    version = str(meta.get("version") or "v")
    raw = f"{slug}-{version}-{int(attempt)}"
    safe = "".join(ch if ch.isalnum() or ch in "._:-" else "-" for ch in raw)
    safe = safe.strip("-") or "skill"
    if len(safe) > 80:
        import hashlib

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        safe = f"skill-{digest}-{int(attempt)}"
    return safe


def _record_path(job_dir):
    return os.path.join(job_dir, "work", RECORD_NAME)


def read_record(job_dir):
    path = _record_path(job_dir)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else None


def write_record(job_dir, record):
    path = _record_path(job_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stored = {
        "hold_id": record.get("hold_id"),
        "order_id": record.get("order_id"),
        "source_item": record.get("source_item"),
        "status": record.get("status"),
        "idempotency_key": record.get("idempotency_key"),
        "attempt": record.get("attempt"),
        "failed": record.get("failed") or [],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    return stored


def open_hold(job_dir, client):
    meta = _job_meta(job_dir)
    previous = read_record(job_dir) or {}
    attempt = int(previous.get("attempt") or 1)
    if previous.get("status") == "released":
        attempt += 1
    key = previous.get("idempotency_key") if previous.get("status") in ("held", "confirmed") else None
    if not key:
        key = idempotency_key(meta, attempt)
    body = client.call("/v1/skill/open", {"idempotency_key": key})
    record = {
        "hold_id": body.get("hold_id"),
        "order_id": body.get("order_id"),
        "source_item": body.get("source_item"),
        "status": body.get("status"),
        "idempotency_key": key,
        "attempt": attempt,
        "failed": [],
    }
    return write_record(job_dir, record)


def confirm(job_dir, client):
    record = read_record(job_dir)
    if not record or not record.get("hold_id"):
        raise SkillCreditError("no skill hold to confirm")
    body = client.call(
        "/v1/skill/confirm",
        {
            "hold_id": record["hold_id"],
            "idempotency_key": record["idempotency_key"],
        },
    )
    record["status"] = body.get("status") or record["status"]
    record["failed"] = []
    return write_record(job_dir, record)


def release(job_dir, client):
    record = read_record(job_dir)
    if not record or not record.get("hold_id"):
        raise SkillCreditError("no skill hold to release")
    body = client.call(
        "/v1/skill/release",
        {
            "hold_id": record["hold_id"],
            "idempotency_key": record["idempotency_key"],
        },
    )
    record["status"] = body.get("status") or "released"
    return write_record(job_dir, record)


def settle(job_dir, client, software=None):
    """Hold one credit, then confirm or release. software() overrides the app check."""
    record = open_hold(job_dir, client)
    if record.get("status") == "released":
        # That key is finished. open_hold moves to the next attempt only after
        # the saved status is released, so this second call does not reuse it.
        record = open_hold(job_dir, client)
    if record.get("status") == "confirmed":
        record["failed"] = []
        return write_record(job_dir, record)
    if record.get("status") != "held":
        raise SkillCreditError("skill run is not held")
    check = illustrator_ready if software is None else software
    failed = []
    try:
        software_ok = bool(check())
    except Exception:
        software_ok = False
    if not software_ok:
        failed.append("doctor")
    if not package_ok(job_dir):
        failed.append("package")
    if not artwork_ok(job_dir):
        failed.append("artwork")
    if failed:
        released = release(job_dir, client)
        released["failed"] = failed
        return write_record(job_dir, released)
    return confirm(job_dir, client)


def main(argv):
    if len(argv) < 1 or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    command = argv[0]
    if command == "doctor":
        return 0 if doctor() else 1
    if len(argv) != 2:
        raise SkillCreditError(f"{command} takes a job directory")
    job_dir = argv[1]
    if command == "lock":
        print(lock_artwork(job_dir))
        return 0
    url, token = load_billing()
    client = BillingClient(url, token)
    if command == "settle":
        result = settle(job_dir, client)
    elif command == "open":
        result = open_hold(job_dir, client)
    elif command == "confirm":
        result = confirm(job_dir, client)
    elif command == "release":
        result = release(job_dir, client)
    else:
        raise SkillCreditError(f"unknown command {command}")
    print(json.dumps({k: result.get(k) for k in ("status", "hold_id", "source_item", "failed")}))
    return 0 if result.get("status") != "released" or command == "release" else 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SkillCreditError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
