"""Discover available models from a provider's upstream gateway.

Most OpenAI-compatible APIs expose ``GET /v1/models`` which lists model ids
the current API key can use. We hit that endpoint and merge the result
into ``provider.model_options`` so the user doesn't have to type model ids
by hand. Anthropic's API has the same shape but different headers.

Failure paths are deliberately user-actionable: timeout / 4xx / unparseable
responses become ``ModelDiscoveryError(reason)`` with a short reason the UI
shows to the user. Discovery never blocks provider creation — the user can
fall back to typing model ids manually.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 5.0
# Ping (chat/completions or messages) needs longer than ``/v1/models``:
# the upstream actually has to dispatch to the LLM and return 1 token,
# which on a cold path through a regional proxy can easily blow past 5s.
# 30s leaves headroom for proxy hops + model cold-start without forcing
# the user to wait too long on a genuinely unreachable endpoint.
PING_TIMEOUT_SECONDS = 30.0
ApiProtocol = Literal["openai", "anthropic"]

# Anthropic returns ids like ``claude-sonnet-4-6-20251015``; the SDK accepts
# the bare alias ``claude-sonnet-4-6`` and looking at multiple snapshots of
# the same model is noise in the picker. Strip a trailing 8-digit date.
_ANTHROPIC_DATE_SUFFIX = re.compile(r"-\d{8}$")


class ModelDiscoveryError(RuntimeError):
    """Discovery failed in a way the user should hear about.

    ``reason`` is a short, user-facing string the API layer surfaces to the
    UI verbatim. Keep it actionable (mentioning the URL or status code is
    fine; stack traces are not).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def discover_models(
    *,
    base_url: str,
    api_key: str,
    protocol: ApiProtocol,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Fetch the model id list from the provider's upstream.

    Parameters
    ----------
    base_url, api_key, protocol
        Provider coordinates. ``base_url`` may or may not end in ``/v1``;
        we try both ``<base>/models`` and ``<base>/v1/models`` in order.
    client
        Optional injected ``httpx.AsyncClient`` for tests / connection
        pooling. When ``None``, a per-call client is created with the
        discovery timeout.

    Returns
    -------
    Sorted, de-duplicated list of model ids. Anthropic ids have any
    trailing date suffix stripped so ``claude-sonnet-4-6-20251015`` becomes
    ``claude-sonnet-4-6``.

    Raises
    ------
    ModelDiscoveryError
        Network errors, non-OK responses, malformed payloads, empty model
        lists, etc. The ``reason`` field is suitable to show to the user.
    """
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        candidates = [f"{base}/models"]
    else:
        candidates = [f"{base}/models", f"{base}/v1/models"]

    headers = _headers_for(protocol, api_key)

    if client is None:
        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as owned_client:
            return await _try_candidates(owned_client, candidates, headers, protocol)
    return await _try_candidates(client, candidates, headers, protocol)


def _headers_for(protocol: ApiProtocol, api_key: str) -> dict[str, str]:
    if protocol == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    return {"Authorization": f"Bearer {api_key}"}


async def _try_candidates(
    client: httpx.AsyncClient,
    candidates: list[str],
    headers: dict[str, str],
    protocol: ApiProtocol,
) -> list[str]:
    # Error messages here are surfaced verbatim in the add-provider
    # dialog (frontend extracts ``detail.reason`` from FastAPI's 422
    # body), so write them as Chinese product copy, not engineer notes.
    last_reason: str | None = None
    for url in candidates:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            raise ModelDiscoveryError("服务方响应超时，请稍后重试") from None
        except httpx.RequestError as exc:
            raise ModelDiscoveryError(f"无法连接到服务方：{exc.__class__.__name__}") from None

        if resp.status_code == 404:
            last_reason = "服务方未提供模型列表接口（/v1/models 不存在）"
            continue

        if resp.status_code in (401, 403):
            raise ModelDiscoveryError("API Key 无效，请检查后重试")

        if resp.status_code == 429:
            raise ModelDiscoveryError("请求过于频繁，请稍后重试")

        if 500 <= resp.status_code < 600:
            raise ModelDiscoveryError(f"服务方异常（HTTP {resp.status_code}），请稍后重试")

        if resp.status_code >= 400:
            raise ModelDiscoveryError(f"服务方拒绝请求（HTTP {resp.status_code}）")

        try:
            payload = resp.json()
        except ValueError:
            last_reason = "服务方返回格式异常，无法解析模型列表"
            continue

        ids = _extract_model_ids(payload, protocol)
        if not ids:
            last_reason = "服务方未返回任何可用模型"
            continue

        return sorted(set(ids))

    raise ModelDiscoveryError(last_reason or "服务方未提供可用的模型列表接口")


def _extract_model_ids(payload: object, protocol: ApiProtocol) -> list[str]:
    """Pull model ids out of an OpenAI / Anthropic-shaped response.

    Both APIs put the list under ``data[]`` with each item exposing ``id``.
    Items missing an ``id`` are skipped (rather than raising) so a
    well-formed list with one bad row still discovers the rest.
    """
    if not isinstance(payload, dict):
        return []
    items = payload.get("data")
    if not isinstance(items, list):
        return []

    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = item.get("id")
        if not isinstance(raw, str) or not raw:
            continue
        if protocol == "anthropic":
            raw = _strip_anthropic_date_suffix(raw)
        ids.append(raw)
    return ids


def _strip_anthropic_date_suffix(model_id: str) -> str:
    """Strip a trailing ``-YYYYMMDD`` date snapshot if present."""
    return _ANTHROPIC_DATE_SUFFIX.sub("", model_id)


async def ping_credentials(
    *,
    base_url: str,
    api_key: str,
    protocol: ApiProtocol,
    model: str,
) -> None:
    """Minimal chat/messages probe to verify a (base_url, api_key, model) tuple.

    Uses the same SDK clients the runtimes use (``anthropic`` /
    ``openai``) so the path strategy ping uses is identical to the path
    strategy a real session would use — no chance of ping succeeding
    against a URL the runtime can't actually reach (or vice versa).
    Lets the SDK handle path composition, header conventions, version
    handshakes, and the OpenAI-vs-Anthropic body shape differences.

    Raises ``ModelDiscoveryError`` on auth / network / upstream failures.
    """
    if not base_url:
        raise ModelDiscoveryError("base_url is required")
    if not api_key:
        raise ModelDiscoveryError("API Key 不能为空")
    if not model:
        raise ModelDiscoveryError("至少需要 1 个模型 id 才能完成连接测试")

    if protocol == "anthropic":
        await _ping_anthropic(base_url=base_url, api_key=api_key, model=model)
    else:
        await _ping_openai(base_url=base_url, api_key=api_key, model=model)


async def _ping_anthropic(*, base_url: str, api_key: str, model: str) -> None:
    # Lazy import — keeps the discover module light when only OpenAI-
    # path code is used (e.g. discover_models). Both SDKs are pulled in
    # transitively by claude-agent-sdk / langchain-openai.
    import anthropic

    client = anthropic.AsyncAnthropic(
        base_url=base_url,
        api_key=api_key,
        timeout=PING_TIMEOUT_SECONDS,
        max_retries=0,
    )
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "."}],
        )
        _assert_model_matches(requested=model, returned=getattr(response, "model", ""))
    except anthropic.AuthenticationError as exc:
        raise ModelDiscoveryError("API Key 无效，请检查后重试") from exc
    except anthropic.NotFoundError as exc:
        # SDK already composed the URL — surface the model id in the
        # message because Anthropic-shape NotFound is almost always
        # "model not found" rather than a real 404 on /v1/messages.
        raise ModelDiscoveryError(
            f"服务方未找到模型「{model}」或该接口（请检查 Endpoint 与模型 id）"
        ) from exc
    except anthropic.BadRequestError as exc:
        # Our ping request is fully canonical (max_tokens=1, single ".")
        # so 400 is almost always "model id not recognised by the
        # upstream". Surface the model id so the user can spot typos
        # like ``mimo-v2.5-pr12`` vs ``mimo-v2.5-pro`` immediately.
        reason = _extract_anthropic_error(exc)
        detail = f"模型「{model}」可能不存在或上游不识别"
        if reason:
            detail = f"{detail}（上游：{reason}）"
        raise ModelDiscoveryError(detail) from exc
    except anthropic.RateLimitError as exc:
        raise ModelDiscoveryError("请求过于频繁，请稍后重试") from exc
    except anthropic.APITimeoutError as exc:
        raise ModelDiscoveryError("服务方响应超时，请稍后重试") from exc
    except anthropic.APIConnectionError as exc:
        raise ModelDiscoveryError(f"无法连接到服务方：{exc.__class__.__name__}") from exc
    except anthropic.APIStatusError as exc:
        # 5xx and any other status the SDK surfaces as APIStatusError.
        if 500 <= exc.status_code < 600:
            raise ModelDiscoveryError(f"服务方异常（HTTP {exc.status_code}），请稍后重试") from exc
        reason = _extract_anthropic_error(exc) or f"HTTP {exc.status_code}"
        raise ModelDiscoveryError(f"服务方拒绝请求：{reason}") from exc


async def _ping_openai(*, base_url: str, api_key: str, model: str) -> None:
    import openai

    client = openai.AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=PING_TIMEOUT_SECONDS,
        max_retries=0,
    )
    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "."}],
        )
        _assert_model_matches(requested=model, returned=getattr(response, "model", ""))
    except openai.AuthenticationError as exc:
        raise ModelDiscoveryError("API Key 无效，请检查后重试") from exc
    except openai.NotFoundError as exc:
        raise ModelDiscoveryError(
            f"服务方未找到模型「{model}」或该接口（请检查 Endpoint 与模型 id）"
        ) from exc
    except openai.BadRequestError as exc:
        reason = _extract_openai_error(exc)
        detail = f"模型「{model}」可能不存在或上游不识别"
        if reason:
            detail = f"{detail}（上游：{reason}）"
        raise ModelDiscoveryError(detail) from exc
    except openai.RateLimitError as exc:
        raise ModelDiscoveryError("请求过于频繁，请稍后重试") from exc
    except openai.APITimeoutError as exc:
        raise ModelDiscoveryError("服务方响应超时，请稍后重试") from exc
    except openai.APIConnectionError as exc:
        raise ModelDiscoveryError(f"无法连接到服务方：{exc.__class__.__name__}") from exc
    except openai.APIStatusError as exc:
        if 500 <= exc.status_code < 600:
            raise ModelDiscoveryError(f"服务方异常（HTTP {exc.status_code}），请稍后重试") from exc
        reason = _extract_openai_error(exc) or f"HTTP {exc.status_code}"
        raise ModelDiscoveryError(f"服务方拒绝请求：{reason}") from exc


def _assert_model_matches(*, requested: str, returned: str) -> None:
    """Catch silent model-substitution by misbehaving proxies.

    Some upstreams (and a fair number of community proxies) accept any
    model id with HTTP 200, then silently fall back to their own default
    when the id isn't one they know about. The SDK call doesn't raise —
    we'd report ping success on a model the user can never actually use.

    Compare the request's ``model`` against the response's ``model`` and
    raise if they don't line up. We accept exact match plus the common
    "request + version suffix" pattern (Anthropic's
    ``claude-sonnet-4-6`` → ``claude-sonnet-4-6-20251015``; OpenAI's
    ``gpt-4o`` → ``gpt-4o-2024-08-06``) — anything else is a substitution.

    Empty ``returned`` means the upstream didn't echo the model field; we
    trust the round-trip in that case rather than fail-loud, since some
    legitimate proxies (and streaming partials with ``max_tokens=1``)
    omit it.
    """
    returned = (returned or "").strip()
    if not returned:
        return
    if returned == requested:
        return
    if returned.startswith(f"{requested}-"):
        return
    raise ModelDiscoveryError(
        f"上游返回了模型「{returned}」而非请求的「{requested}」，"
        "服务方可能不支持该模型 id（已 fallback 到其他模型）"
    )


@dataclass
class PingBatchResult:
    """Outcome of a per-model ping batch.

    ``ok`` lists the models that returned a usable response (echoing the
    requested id). ``failed`` lists (model_id, reason) tuples for the
    ones that errored or substituted. Service / API callers use the
    split to persist only the working ids and surface the rest as
    diagnostics.
    """

    ok: list[str]
    failed: list[tuple[str, str]]


async def ping_credentials_batch(
    *,
    base_url: str,
    api_key: str,
    protocol: ApiProtocol,
    models: list[str],
) -> PingBatchResult:
    """Ping every model in ``models`` and partition by outcome.

    Each model gets its own ``ping_credentials`` call. Failures become
    ``(model, reason)`` rows in ``failed`` rather than aborting the
    whole batch — the caller wants to know which subset works so it can
    persist the working ids and let the user prune the rest.

    Runs sequentially. Parallel via ``asyncio.gather`` is faster but
    risks tripping upstream rate limits on small free-tier proxies; a
    handful of 1-token requests in series is plenty fast in practice
    and avoids that footgun entirely.
    """
    ok: list[str] = []
    failed: list[tuple[str, str]] = []
    for m in models:
        try:
            await ping_credentials(
                base_url=base_url,
                api_key=api_key,
                protocol=protocol,
                model=m,
            )
            ok.append(m)
        except ModelDiscoveryError as exc:
            failed.append((m, exc.reason))
    return PingBatchResult(ok=ok, failed=failed)


def _extract_anthropic_error(exc: object) -> str | None:
    """Pull a user-facing message out of an Anthropic SDK error.

    The SDK exposes ``body`` (parsed JSON) and ``response`` (raw httpx);
    prefer the parsed body so we surface the upstream's actual message.
    """
    body = getattr(exc, "body", None)
    return _extract_error_reason(body)


def _extract_openai_error(exc: object) -> str | None:
    """Same shape as anthropic — both SDKs put errors under ``body.error``."""
    body = getattr(exc, "body", None)
    return _extract_error_reason(body)


def _extract_error_reason(payload: object) -> str | None:
    """Best-effort pull of an upstream error message string.

    Both OpenAI and Anthropic surface errors as ``{"error": {"message": "..."}}``
    or ``{"error": "..."}``. Returns ``None`` if neither shape matches —
    caller substitutes a generic fallback.
    """
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        return msg if isinstance(msg, str) and msg else None
    if isinstance(err, str) and err:
        return err
    return None


__all__ = [
    "ApiProtocol",
    "DISCOVERY_TIMEOUT_SECONDS",
    "PING_TIMEOUT_SECONDS",
    "ModelDiscoveryError",
    "PingBatchResult",
    "discover_models",
    "ping_credentials",
    "ping_credentials_batch",
]
