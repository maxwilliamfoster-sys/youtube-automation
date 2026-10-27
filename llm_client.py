# vendored from growth-lab/llm_client.py @ c0d30e9
"""llm_client: one free-tier LLM router for every bot (growth-lab S2, master copy).

Edit it HERE, then roll it out with sync_vendored.py. Stdlib only.

What it does
------------
* Groq first (its OpenAI-compatible REST endpoint), then Gemini's free
  OpenAI-compatible endpoint when GOOGLE_API_KEY is set.
* Fall through to the NEXT model straight away on 404 (retired model), 429,
  5xx, a timeout, a dropped connection or an empty reply. Raise LLMFatal at once
  on 400/401/403: switching model will not fix a bad request or a bad key.
* At most ONE back-off: when a pass ends with nothing answered and at least one
  model failed for a passing reason (per-minute 429, 5xx, timeout), sleep once
  (Retry-After when the server sent one, else 25 s, capped by `max_wait`) and
  retry only those models. A per-DAY 429 or a 404 is never retried.
* Groq budget pre-flight (GroqBudget + preflight()): lets a run stop with a
  "quota" outcome BEFORE it renders anything.

Groq budgets are PER MODEL (critic correction, 2026-10-21 audit). Each model id
has its own bucket, metered per organisation, so every bot using the same org
shares each model's bucket. Free-plan base limits from
https://console.groq.com/docs/rate-limits (checked 2026-10-23):

    openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b:
        30 RPM, 1K RPD, 8K TPM, 200K TPD  (each model separately)

Headers on every Groq reply: x-ratelimit-limit/remaining/reset-requests (RPD)
and x-ratelimit-limit/remaining/reset-tokens (TPM). `retry-after` (seconds)
only on a 429. TPD is NOT in any header, so the budget is a local per-day
ledger (UTC day) plus two exact signals: an RPD header of 0, and the
"Limit 200000, Used 199500" text of a TPD 429, which reports the whole org's use.

Swap-in for youtube-automation/story_generator.py
-------------------------------------------------
`Client()` mimics the groq SDK object those call sites use:

    client = llm_client.Client(fallback_models=["openai/gpt-oss-20b"],
                               exclude_models={"qwen/qwen3.8-27b"},
                               budget=llm_client.GroqBudget("groq_budget.json"))
    resp = client.chat.completions.create(model=GROQ_MODEL, max_tokens=600, messages=[...])
    text = resp.choices[0].message.content

Pre-flight in a workflow, before rendering (exit code 3 = no budget):

    py llm_client.py preflight --ledger groq_budget.json \
        --need 30000:openai/gpt-oss-120b,openai/gpt-oss-20b --need 9000:qwen/qwen3.8-27b
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from types import SimpleNamespace

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

GROQ_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")
# Rolling -latest aliases first: Google retires pinned ids (gemini-2.5-flash 404'd on 2026-10-20).
GEMINI_MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.1-flash-lite")
# reasoning_effort "low" lets Gemini think for up to 1024 tokens, and those count against
# max_tokens. Added on top of the caller's budget so the answer keeps its own room
# (a 600-token writer budget capped at 1024 left BCF scripts at ~25 words on 2026-10-27).
GEMINI_THINKING_TOKENS = 1024

# Tokens per day, per model, per organisation (Groq free plan base limits).
GROQ_TPD = {
    "openai/gpt-oss-120b": 200_000,
    "openai/gpt-oss-20b": 200_000,
    "qwen/qwen3.8-27b": 200_000,
}
DEFAULT_TPD = 200_000

USER_AGENT = "growth-lab-llm-client/1.0"
FATAL = {400, 401, 403}
DEFAULT_WAIT = 25.0
TIMEOUT = 90


class LLMError(RuntimeError):
    pass


class LLMFatal(LLMError):
    """400/401/403: the request or key is wrong. Do not fall through."""

    def __init__(self, msg, status=None, model=None):
        super().__init__(msg)
        self.status, self.model = status, model


class LLMUnavailable(LLMError):
    """No backend answered (after the single back-off). `attempts` lists why."""

    def __init__(self, msg, attempts=()):
        super().__init__(msg)
        self.attempts = list(attempts)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_duration(v):
    """Groq reset values: '2m59.56s', '7.66s', '1h2m3s', or plain seconds. None if absent."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", s):
        found = True
        total += float(num) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total if found else None


def _is_daily(text):
    t = (text or "").lower()
    return ("per day" in t or "(tpd)" in t or "(rpd)" in t
            or re.search(r"\btpd\b|\brpd\b", t) is not None)


_ORG_USE = re.compile(r"Limit\s+(\d+),\s*Used\s+(\d+)", re.I)


# ---------------------------------------------------------------- transport --

def _http_post(url, body, headers, timeout):
    """POST JSON. Returns (status, headers{lowercase: value}, text).
    Raises TimeoutError / OSError on timeouts and connection failures."""
    data = json.dumps(body).encode("utf-8")
    # Groq sits behind Cloudflare, which answers urllib's default "Python-urllib/3.x"
    # User-Agent with 403 "error code: 1010" (checked 2026-10-23). Always send our own.
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, \
                r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        return e.code, hdrs, text


# ------------------------------------------------------------------- budget --

class GroqBudget:
    """Per-model, per-UTC-day Groq token ledger kept in a small JSON file.

    It sees only what THIS bot spends, plus two org-wide signals (an RPD header
    of 0, and the 'Used N' figure in a TPD 429). `share` caps this bot's slice of
    each model's TPD, so bots on the same org can split a bucket (e.g. 0.5)."""

    def __init__(self, path, limits=None, share=1.0, today=None):
        self.path = path
        self.limits = dict(GROQ_TPD, **(limits or {}))
        self.share = float(share)
        self.day = today or _today()
        self.data = {"day": self.day, "models": {}}
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("day") == self.day and isinstance(d.get("models"), dict):
                self.data = d
        except (OSError, ValueError):
            pass

    def _m(self, model):
        return self.data["models"].setdefault(model, {"tokens": 0, "requests": 0})

    def limit(self, model):
        return int(self.limits.get(model, DEFAULT_TPD) * self.share)

    def used(self, model):
        return int(self.data["models"].get(model, {}).get("tokens", 0))

    def record(self, model, tokens, headers=None):
        m = self._m(model)
        m["tokens"] = int(m.get("tokens", 0)) + int(tokens or 0)
        m["requests"] = int(m.get("requests", 0)) + 1
        if headers:
            self.update_from_headers(model, headers)

    def update_from_headers(self, model, headers):
        m = self._m(model)
        for key, field in (("x-ratelimit-remaining-requests", "rpd_remaining"),
                           ("x-ratelimit-remaining-tokens", "tpm_remaining")):
            v = headers.get(key)
            if v is not None:
                try:
                    m[field] = int(float(v))
                except ValueError:
                    pass
        reset = parse_duration(headers.get("x-ratelimit-reset-requests"))
        if reset is not None:
            m["rpd_reset_s"] = reset

    def note_org_usage(self, model, used, limit=None):
        m = self._m(model)
        m["org_used"] = int(used)
        if limit:
            m["org_limit"] = int(limit)

    def mark_exhausted(self, model):
        self._m(model)["exhausted"] = True

    def remaining(self, model):
        m = self.data["models"].get(model, {})
        if m.get("exhausted") or m.get("rpd_remaining") == 0:
            return 0
        own = self.limit(model) - int(m.get("tokens", 0))
        if "org_used" in m:  # the org-wide figure from a 429 is the better number
            org_left = int(m.get("org_limit", self.limits.get(model, DEFAULT_TPD))) - m["org_used"]
            own = min(own, org_left)
        return max(0, own)

    def can_afford(self, model, tokens):
        return self.remaining(model) >= tokens

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


def preflight(budget, needs):
    """needs: [(tokens, [model alternatives...]), ...], one entry per role
    (script writing, gate review...). Each role must fit on at least one of its
    models; roles sharing a model draw on the same bucket. Returns
    SimpleNamespace(ok, reason, plan={role_index: model})."""
    left = {}
    plan = {}
    for i, (tokens, models) in enumerate(needs):
        pick = None
        for m in models:
            if m not in left:
                left[m] = budget.remaining(m)
            if left[m] >= tokens:
                pick = m
                break
        if pick is None:
            have = ", ".join(f"{m}={left.get(m, budget.remaining(m))}" for m in models)
            return SimpleNamespace(ok=False, plan=plan,
                                   reason=f"Groq budget exhausted: need {tokens} tokens on one of "
                                          f"[{have}] (UTC day {budget.day})")
        left[pick] -= tokens
        plan[i] = pick
    return SimpleNamespace(ok=True, plan=plan, reason="ok")


# --------------------------------------------------------------------- chat --

def _groq_body(model, messages, kw):
    body = {"model": model, "messages": messages}
    for k in ("max_tokens", "temperature", "top_p", "stop", "response_format", "seed"):
        if kw.get(k) is not None:
            body[k] = kw[k]
    if "gpt-oss" in model:  # Groq accepts reasoning_effort on gpt-oss only (else 400)
        body["reasoning_effort"] = kw.get("reasoning_effort") or "low"
    return body


def _gemini_body(model, messages, kw):
    body = {"model": model, "messages": messages,
            "max_tokens": int(kw.get("max_tokens") or 1400) + GEMINI_THINKING_TOKENS,
            "reasoning_effort": "low"}
    if kw.get("temperature") is not None:
        body["temperature"] = kw["temperature"]
    return body


def _wrap(text, provider, model, usage, headers, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        provider=provider, model=model, headers=headers, finish_reason=finish_reason,
        usage=SimpleNamespace(total_tokens=int((usage or {}).get("total_tokens") or 0),
                              prompt_tokens=int((usage or {}).get("prompt_tokens") or 0),
                              completion_tokens=int((usage or {}).get("completion_tokens") or 0)))


def chat(messages, groq_models=None, gemini_models=None, budget=None, exclude_models=(),
         max_wait=30.0, timeout=TIMEOUT, sleep=time.sleep, log=print, **kw):
    """Send one chat request through the fall-through chain. Returns an object
    shaped like the groq/openai SDK reply (resp.choices[0].message.content),
    plus .provider, .model, .usage and .headers. Raises LLMFatal / LLMUnavailable."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    gem_key = os.environ.get("GOOGLE_API_KEY", "")
    excluded = set(exclude_models or ())
    cands = []
    if groq_key:
        cands += [("groq", m) for m in (groq_models or GROQ_MODELS) if m not in excluded]
    if gem_key:
        cands += [("gemini", m) for m in (gemini_models or GEMINI_MODELS) if m not in excluded]
    if not cands:
        raise LLMUnavailable("no LLM backend configured (GROQ_API_KEY / GOOGLE_API_KEY)")

    attempts = []
    todo = cands
    for pass_no in (1, 2):
        transient, waits = [], []
        for provider, model in todo:
            if provider == "groq" and budget is not None and budget.remaining(model) <= 0:
                attempts.append((model, "skipped: budget spent today"))
                continue
            url, key = (GROQ_URL, groq_key) if provider == "groq" else (GEMINI_URL, gem_key)
            body = (_groq_body if provider == "groq" else _gemini_body)(model, messages, kw)
            try:
                status, headers, text = _http_post(url, body, {"Authorization": f"Bearer {key}"},
                                                   timeout)
            except (TimeoutError, OSError) as e:  # socket.timeout and URLError are OSErrors
                attempts.append((model, f"{type(e).__name__}: {str(e)[:80]}"))
                log(f"[llm] {provider} {model}: {type(e).__name__} - trying the next model")
                transient.append((provider, model))
                continue
            if provider == "groq" and budget is not None and headers:
                budget.update_from_headers(model, headers)
            if status == 200:
                try:
                    j = json.loads(text)
                    content = (j["choices"][0]["message"].get("content") or "").strip()
                except (ValueError, KeyError, IndexError, TypeError):
                    j, content = {}, ""
                if content:
                    if provider == "groq" and budget is not None:
                        budget.record(model, (j.get("usage") or {}).get("total_tokens", 0))
                        _save(budget, log)
                    finish = "length" if j["choices"][0].get("finish_reason") == "length" else None
                    if finish:
                        log(f"[llm] {provider} {model}: reply cut off at max_tokens "
                            f"({len(content.split())} words)")
                    return _wrap(content, provider, model, j.get("usage"), headers, finish)
                attempts.append((model, "empty reply"))
                log(f"[llm] {provider} {model}: empty reply - trying the next model")
                transient.append((provider, model))
                continue
            short = (text or "")[:160]
            attempts.append((model, f"HTTP {status}: {short[:80]}"))
            if status in FATAL:
                _save(budget, log)
                raise LLMFatal(f"{provider} {model} HTTP {status}: {short}", status, model)
            if status == 429 and _is_daily(text):
                log(f"[llm] {provider} {model}: daily cap reached - next model")
                if provider == "groq" and budget is not None:
                    budget.mark_exhausted(model)
                    m = _ORG_USE.search(text or "")
                    if m:
                        budget.note_org_usage(model, used=int(m.group(2)), limit=int(m.group(1)))
                continue
            if status == 404:
                log(f"[llm] {provider} {model}: HTTP 404 (retired?) - next model")
                continue
            log(f"[llm] {provider} {model}: HTTP {status} - next model")
            transient.append((provider, model))
            ra = parse_duration(headers.get("retry-after")) if headers else None
            if ra is not None:
                waits.append(ra)
        _save(budget, log)
        if pass_no == 1 and transient:
            wait = max(waits) if waits else DEFAULT_WAIT
            wait = float(max(1.0, min(wait, max_wait)))
            log(f"[llm] every model failed; one back-off of {wait:.0f}s, then one more pass")
            sleep(wait)
            todo = transient
            continue
        break
    why = "; ".join(f"{m}: {r}" for m, r in attempts)[:400]
    raise LLMUnavailable(f"every model unavailable ({why})", attempts)


def _save(budget, log):
    if budget is None:
        return
    try:
        budget.save()
    except OSError as e:
        log(f"[llm] budget ledger not saved ({e})")


class Client:
    """Drop-in for `groq.Groq(api_key=...)` at call sites that only use
    client.chat.completions.create(model=..., messages=..., max_tokens=..., ...).
    `model` becomes the first Groq model; `fallback_models` follow it."""

    def __init__(self, fallback_models=(), gemini_models=None, budget=None, exclude_models=(),
                 max_wait=30.0, sleep=time.sleep, log=print):
        self._opts = dict(gemini_models=gemini_models, budget=budget,
                          exclude_models=exclude_models, max_wait=max_wait, sleep=sleep, log=log)
        self._fallback = list(fallback_models)
        outer = self

        class _Completions:
            def create(self, model=None, messages=None, **kw):
                order = [m for m in [model] + outer._fallback if m]
                order = list(dict.fromkeys(order)) or list(GROQ_MODELS)
                return chat(messages or [], groq_models=order, **outer._opts, **kw)

        self.chat = SimpleNamespace(completions=_Completions())


# ---------------------------------------------------------------------- CLI --

def main(argv=None):
    ap = argparse.ArgumentParser(description="Groq budget pre-flight (exit 3 = no budget)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("--ledger", required=True)
    p.add_argument("--need", action="append", required=True,
                   help="TOKENS:model[,model...] (one per role)")
    p.add_argument("--share", type=float, default=1.0)
    a = ap.parse_args(argv)
    budget = GroqBudget(a.ledger, share=a.share, today=_today())
    needs = []
    for n in a.need:
        tok, _, models = n.partition(":")
        needs.append((int(tok), [m for m in models.split(",") if m] or list(GROQ_MODELS)))
    res = preflight(budget, needs)
    print(f"[llm] preflight: {res.reason}")
    return 0 if res.ok else 3


if __name__ == "__main__":
    sys.exit(main())
