"""Zentrale Provider-Schicht für alle KI-Aufrufe.

Kapselt Modell-Katalog, API-Key-Verwaltung (inkl. Backup-Keys), Modell-Gewichte
und die eigentliche Generierung (JSON / Text / Stream) über alle Provider:
  - Google Gemini      -> GEMINI_API_KEY (google-genai SDK)
  - Groq               -> GROQ_API_KEY
  - OpenRouter         -> OPENROUTER_API_KEY + OPENROUTER_API_KEY_BACKUP
  - Mistral            -> MISTRAL_API_KEY
  - GitHub Models      -> GITHUB_MODELS_TOKEN ("Copilot-Modelle", free tier)
  - Cerebras           -> CEREBRAS_API_KEY (free tier, extrem schnell)

Backup-Keys: Für jeden OpenAI-kompatiblen Provider wird zusätzlich
`<ENV>_BACKUP` geprüft. Ist der primäre Key rate-limited (z.B. Tages-Maximum
bei OpenRouter), wird die komplette Modell-Kette mit dem Backup-Key wiederholt.
"""
import os
import logging
from typing import AsyncIterator, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Erlaubte Modelle je Provider (alle mit kostenlosem Free-Tier).
ALLOWED_MODELS = {
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
    ],
    "openrouter": [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "google/gemma-4-31b-it:free",
        "openai/gpt-oss-20b:free",
    ],
    "mistral": [
        "mistral-small-latest",
        "open-mistral-nemo",
    ],
    # GitHub Models – kostenlose "Copilot"-Modelle per GitHub-Token
    "github": [
        "openai/gpt-4.1",
        "openai/gpt-4.1-mini",
        "openai/gpt-4o-mini",
    ],
    # Cerebras – free tier, sehr schnelle Inferenz
    "cerebras": [
        "gpt-oss-120b",
        "llama-3.3-70b",
        "qwen-3-32b",
    ],
}

# Fallback-Reihenfolge innerhalb eines Providers (bei 429 nächstes Modell).
FALLBACK_ORDER = {
    "gemini": ["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-3.1-flash-lite"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen/qwen3-32b"],
    "openrouter": [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "google/gemma-4-31b-it:free",
        "openai/gpt-oss-20b:free",
    ],
    "mistral": ["mistral-small-latest", "open-mistral-nemo"],
    "github": ["openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "cerebras": ["gpt-oss-120b", "llama-3.3-70b", "qwen-3-32b"],
}

# OpenAI-kompatible Backends: base_url + Env-Keys (Reihenfolge = Prio,
# Backup-Key wird automatisch als "<ENV>_BACKUP" ergänzt).
OPENAI_COMPAT_PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "env_keys": ["GROQ_API_KEY"]},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "env_keys": ["OPENROUTER_API_KEY"]},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "env_keys": ["MISTRAL_API_KEY"]},
    "github": {"base_url": "https://models.github.ai/inference", "env_keys": ["GITHUB_MODELS_TOKEN"]},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "env_keys": ["CEREBRAS_API_KEY"]},
}

# Modell-Stärke (1 = leicht, 2 = mittel, 3 = stark). Lektionen & Analysen
# stärkerer Modelle bekommen im System mehr Gewicht.
MODEL_WEIGHTS = {
    "gemini-3.1-pro-preview": 3,
    "gemini-3.5-flash": 2,
    "gemini-3.1-flash-lite": 1,
    "llama-3.3-70b-versatile": 3,
    "llama-3.1-8b-instant": 1,
    "qwen/qwen3-32b": 2,
    "nvidia/nemotron-3-super-120b-a12b:free": 2,
    "nvidia/nemotron-3-ultra-550b-a55b:free": 3,
    "google/gemma-4-31b-it:free": 2,
    "openai/gpt-oss-20b:free": 1,
    "mistral-small-latest": 2,
    "open-mistral-nemo": 1,
    "openai/gpt-4.1": 3,
    "openai/gpt-4.1-mini": 2,
    "openai/gpt-4o-mini": 2,
    "gpt-oss-120b": 3,
    "llama-3.3-70b": 3,
    "qwen-3-32b": 2,
}

WEIGHT_LABELS = {1: "basis", 2: "mittel", 3: "hoch"}


def model_weight(model: Optional[str]) -> int:
    return MODEL_WEIGHTS.get(model or "", 2)


def weight_label(model: Optional[str]) -> str:
    return WEIGHT_LABELS[model_weight(model)]


def provider_for_model(model: str) -> Optional[str]:
    for prov, models in ALLOWED_MODELS.items():
        if model in models:
            return prov
    return None


def is_rate_limit_error(err: Exception) -> bool:
    s = str(err).lower()
    return any(k in s for k in ("429", "resource_exhausted", "quota", "rate limit",
                                "ratelimit", "too many requests"))


def provider_keys(provider: str) -> List[str]:
    """Alle verfügbaren Keys eines Providers in Prio-Reihenfolge (primär, backup)."""
    keys: List[str] = []
    if provider == "gemini":
        env_names = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY_BACKUP"]
    else:
        meta = OPENAI_COMPAT_PROVIDERS.get(provider)
        if not meta:
            return []
        env_names = []
        for e in meta["env_keys"]:
            env_names.append(e)
            env_names.append(f"{e}_BACKUP")
    for name in env_names:
        v = os.environ.get(name)
        if v and v not in keys:
            keys.append(v)
    return keys


def primary_key(provider: str) -> Optional[str]:
    keys = provider_keys(provider)
    return keys[0] if keys else None


def available_providers() -> Dict[str, bool]:
    out = {"gemini": bool(provider_keys("gemini"))}
    for p in OPENAI_COMPAT_PROVIDERS:
        out[p] = bool(provider_keys(p))
    return out


def backup_keys_info() -> Dict[str, bool]:
    """Welche Provider haben einen Backup-Key gesetzt? (fürs Frontend)"""
    out = {}
    out["gemini"] = bool(os.environ.get("GEMINI_API_KEY_BACKUP"))
    for p, meta in OPENAI_COMPAT_PROVIDERS.items():
        out[p] = any(os.environ.get(f"{e}_BACKUP") for e in meta["env_keys"])
    return out


def same_provider_chain(provider: str, preferred: Optional[str]) -> List[Tuple[str, str]]:
    """Bevorzugtes Modell zuerst, dann die restlichen des Providers."""
    allowed = ALLOWED_MODELS.get(provider, [])
    if not allowed:
        return []
    pref = preferred if preferred in allowed else allowed[0]
    order = FALLBACK_ORDER.get(provider, allowed)
    chain = [pref] + [m for m in order if m != pref]
    return [(provider, m) for m in chain if m in allowed]


# ---------------- Provider-Health (Limit-/Fallback-Anzeige) ----------------
# Sichtbar machen, WARUM die KI auf ein anderes Modell ausgewichen ist.
RATE_LIMIT_COOLDOWN_S = 30 * 60

_health: Dict[str, Dict] = {}     # "provider/model" -> {status, ts, detail, key_index}
_last_call: Dict = {}             # letzter erfolgreicher Aufruf inkl. Fallback-Info


def _now() -> float:
    import time as _t
    return _t.time()


def record_result(provider: str, model: str, status: str, detail: str = "",
                  key_index: int = 0, role: Optional[str] = None,
                  requested: Optional[str] = None):
    """Ergebnis eines Modell-Aufrufs festhalten (ok | rate_limited | error)."""
    _health[f"{provider}/{model}"] = {
        "provider": provider, "model": model, "status": status,
        "detail": str(detail)[:200], "key_index": key_index, "ts": _now(),
    }
    if status == "ok":
        _last_call.update({
            "provider": provider, "model": model, "role": role,
            "requested_model": requested, "key_index": key_index,
            "fallback": bool((requested and requested != model) or key_index > 0),
            "ts": _now(),
        })


def health_status() -> Dict:
    """Aufbereiteter Zustand für /api/ai/status und die UI."""
    now = _now()
    limited, errors, models = [], [], {}
    for key, h in _health.items():
        age = now - float(h.get("ts", 0))
        entry = {**h, "age_s": int(age)}
        if h.get("status") == "rate_limited":
            entry["cooldown_left_s"] = max(0, int(RATE_LIMIT_COOLDOWN_S - age))
            if entry["cooldown_left_s"] > 0:
                limited.append(entry)
        elif h.get("status") == "error" and age < RATE_LIMIT_COOLDOWN_S:
            errors.append(entry)
        models[key] = entry
    last = dict(_last_call)
    if last.get("ts"):
        last["age_s"] = int(now - last["ts"])
    return {
        "models": models,
        "rate_limited": limited,
        "errors": errors,
        "last_call": last,
        "fallback_active": bool(last.get("fallback")),
        "providers": available_providers(),
        "backup_keys": backup_keys_info(),
    }


# ---------------- client caches ----------------
_gemini_clients: Dict[str, object] = {}   # key -> genai.Client
_oai_clients: Dict[Tuple[str, str], object] = {}  # (provider, key) -> AsyncOpenAI


def _gemini_client(key: str):
    cl = _gemini_clients.get(key)
    if cl is None:
        from google import genai
        cl = genai.Client(api_key=key)
        _gemini_clients[key] = cl
    return cl


def _oai_client(provider: str, key: str):
    ck = (provider, key)
    cl = _oai_clients.get(ck)
    if cl is None:
        from openai import AsyncOpenAI
        meta = OPENAI_COMPAT_PROVIDERS[provider]
        default_headers = None
        if provider == "openrouter":
            default_headers = {
                "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://krypto-alert.local"),
                "X-Title": os.environ.get("OPENROUTER_TITLE", "Krypto Alert KI Trader"),
            }
        cl = AsyncOpenAI(base_url=meta["base_url"], api_key=key, default_headers=default_headers)
        _oai_clients[ck] = cl
    return cl


# ---------------- generation ----------------
async def _gemini_generate(model: str, key: str, prompt: str, system: str,
                           temperature: float, json_mode: bool) -> str:
    from google.genai import types
    client = _gemini_client(key)
    cfg = dict(system_instruction=system, temperature=temperature)
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    resp = await client.aio.models.generate_content(
        model=model, contents=prompt, config=types.GenerateContentConfig(**cfg))
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError(f"Leere Antwort von gemini/{model}")
    return text


async def _oai_generate(provider: str, model: str, key: str, prompt: str, system: str,
                        temperature: float, json_mode: bool) -> str:
    client = _oai_client(provider, key)
    kwargs = dict(model=model,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": prompt}],
                  temperature=temperature)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = await client.chat.completions.create(**kwargs)
    except Exception as inner:
        if json_mode and ("response_format" in str(inner).lower() or "json_object" in str(inner).lower()):
            kwargs.pop("response_format", None)
            resp = await client.chat.completions.create(**kwargs)
        else:
            raise
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError(f"Leere Antwort von {provider}/{model}")
    return text


async def generate_chain(chain: List[Tuple[str, str]], prompt: str, system: str,
                         temperature: float = 0.4, json_mode: bool = True) -> Tuple[str, str, str]:
    """Iteriert (provider, model)-Kette; pro Provider alle Keys (primär -> backup).

    Rate-Limits führen zum nächsten Key bzw. Modell; andere Fehler zum nächsten
    Ketten-Eintrag. Rückgabe: (text, provider, model). Wirft den letzten Fehler,
    wenn die gesamte Kette scheitert."""
    last_err: Optional[Exception] = None
    tried_any = False
    for provider, model in chain:
        keys = provider_keys(provider)
        if not keys:
            continue
        for i, key in enumerate(keys):
            tried_any = True
            try:
                if provider == "gemini":
                    text = await _gemini_generate(model, key, prompt, system, temperature, json_mode)
                else:
                    text = await _oai_generate(provider, model, key, prompt, system, temperature, json_mode)
                if i > 0:
                    logger.warning(f"AI: Backup-Key für {provider} genutzt ({model})")
                record_result(provider, model, "ok", key_index=i,
                              requested=chain[0][1] if chain else None)
                return text, provider, model
            except Exception as e:
                last_err = e
                if is_rate_limit_error(e):
                    logger.warning(f"{provider}/{model} rate-limited (Key {i + 1}/{len(keys)}), weiter…")
                    record_result(provider, model, "rate_limited", str(e), key_index=i)
                    continue
                logger.warning(f"{provider}/{model} Fehler: {str(e)[:150]} – nächstes Modell…")
                record_result(provider, model, "error", str(e), key_index=i)
                break  # anderer Fehler -> nächstes Modell, nicht nächster Key
    if not tried_any:
        raise RuntimeError("Kein API-Key für die konfigurierten Provider gesetzt")
    raise last_err or RuntimeError("Alle Modelle der Kette fehlgeschlagen")


async def stream_chain(chain: List[Tuple[str, str]], prompt: str, system: str,
                       temperature: float = 0.6):
    """Streaming über die Kette. Yields ('token', str) Chunks, danach einmal
    ('meta', (provider, model)). Bei komplettem Scheitern ('error', msg)."""
    last_err: Optional[Exception] = None
    tried_any = False
    for provider, model in chain:
        keys = provider_keys(provider)
        if not keys:
            continue
        for i, key in enumerate(keys):
            tried_any = True
            streamed = False
            try:
                if provider == "gemini":
                    from google.genai import types
                    client = _gemini_client(key)
                    stream = await client.aio.models.generate_content_stream(
                        model=model, contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system, temperature=temperature))
                    async for chunk in stream:
                        part = getattr(chunk, "text", None)
                        if part:
                            streamed = True
                            yield ("token", part)
                else:
                    client = _oai_client(provider, key)
                    stream = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": prompt}],
                        temperature=temperature, stream=True)
                    async for chunk in stream:
                        try:
                            part = chunk.choices[0].delta.content
                        except Exception:
                            part = None
                        if part:
                            streamed = True
                            yield ("token", part)
                record_result(provider, model, "ok", key_index=i,
                              requested=chain[0][1] if chain else None)
                yield ("meta", (provider, model))
                return
            except Exception as e:
                last_err = e
                if streamed:
                    yield ("error", f"KI-Fehler: {str(e)[:200]}")
                    return
                if is_rate_limit_error(e):
                    logger.warning(f"{provider}/{model} chat rate-limited (Key {i + 1}), weiter…")
                    record_result(provider, model, "rate_limited", str(e), key_index=i)
                    continue
                logger.warning(f"{provider}/{model} chat Fehler: {str(e)[:150]}")
                record_result(provider, model, "error", str(e), key_index=i)
                break
    if not tried_any:
        yield ("error", "Kein API-Key für die konfigurierten Provider gesetzt")
        return
    yield ("error", f"Alle Modelle rate-limited/fehlgeschlagen. {str(last_err)[:150]}")
