"""PaddleOCR-VL plugin: descriptor + async-poll backend.

The hosted PaddleOCR API at ``paddleocr.aistudio-app.com/api/v2/ocr/jobs``
is async (job creation → poll → JSONL download), so the plugin runs in
``ASYNC_POLL`` mode through ``PollingScheduler`` — same shape as MinerU.

Locked decisions per plan §"关键设计决策":

- Endpoint + model are constants (not user-configurable). The settings
  page exposes only the API token + a few processing toggles.
"""

from __future__ import annotations

from plugins.parser.paddleocr.handler import (
    PADDLEOCR_HANDLER_KIND,
    PaddleOcrPollingHandler,
)
from valuz_agent.modules.parser.polling import PollingScheduler
from valuz_agent.ports.parser_backend import ParseOptions, ParserBackend, ParseResult
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ConfigField,
    ParserPlugin,
    ParserPluginConfig,
    ParserPluginDescriptor,
    ParserPluginMode,
    PluginCapability,
    SecretResolver,
    SetupRequirement,
)

PADDLEOCR_PLUGIN_ID = "paddleocr"

_PADDLEOCR_SETUP = SetupRequirement(
    id="paddleocr_credentials",
    label_zh="PaddleOCR-VL API Token",
    label_key="parser_paddleocr.setup.label",
    kind="credential",
    network_required=False,
    source="aistudio.baidu.com",
    license_name="AI Studio 服务条款",
    license_url="https://aistudio.baidu.com/clauseAndPrivacy",
)

_DESCRIPTOR = ParserPluginDescriptor(
    id=PADDLEOCR_PLUGIN_ID,
    name_zh="PaddleOCR(云端,PaddleOCR-VL-1.6)",
    description_zh=(
        "百度 AI Studio 上的 PaddleOCR-VL-1.6 高精度模型,异步 API。"
        "仅支持 PDF 和图片解析;Office / Excel / HTML 会自动走本地解析。"
    ),
    name_key="parser_paddleocr.descriptor.name",
    description_key="parser_paddleocr.descriptor.description",
    i18n_namespace="parser_paddleocr",
    sort_weight=30,
    mode=ParserPluginMode.ASYNC_POLL,
    capabilities=(
        PluginCapability(
            kind="pdf",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_PADDLEOCR_SETUP,
        ),
        PluginCapability(
            kind="image",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_PADDLEOCR_SETUP,
        ),
    ),
    config_schema=(
        ConfigField(
            key="access_token",
            label_zh="Access Token",
            label_key="parser_paddleocr.config.access_token.label",
            type="secret",
            required=True,
            help_zh="AI Studio 个人设置 → 访问令牌",
            help_key="parser_paddleocr.config.access_token.help",
            help_url="https://aistudio.baidu.com/account/accessToken",
        ),
        ConfigField(
            key="use_doc_orientation_classify",
            label_zh="文档方向识别",
            label_key="parser_paddleocr.config.use_doc_orientation_classify.label",
            type="bool",
            default=False,
        ),
        ConfigField(
            key="use_doc_unwarping",
            label_zh="文档去扭曲",
            label_key="parser_paddleocr.config.use_doc_unwarping.label",
            type="bool",
            default=False,
        ),
        ConfigField(
            key="use_chart_recognition",
            label_zh="图表识别",
            label_key="parser_paddleocr.config.use_chart_recognition.label",
            type="bool",
            default=False,
        ),
    ),
    fallback_policy="allow_local_fallback",
)


class _PaddleOcrBackend(ParserBackend):
    """Enqueues parses on the shared ``PollingScheduler`` and awaits
    completion. Mirrors ``MineruBackend`` — the request shape lives in
    ``PaddleOcrPollingHandler``."""

    def __init__(
        self,
        *,
        config: ParserPluginConfig,
        secret_resolver: SecretResolver,
        scheduler: PollingScheduler,
    ) -> None:
        self._config = config
        self._secrets = secret_resolver
        self._scheduler = scheduler

    async def parse(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        token = self._secrets.resolve(self._config.secret_ref)
        if not token:
            raise RuntimeError("PaddleOCR access token is not configured")

        payload = {
            "file_path": file_path,
            "token": token,
            "options": dict(self._config.options),
        }
        task_id = await self._scheduler.enqueue(PADDLEOCR_HANDLER_KIND, payload)
        result = await self._scheduler.await_task(task_id)
        return ParseResult(
            markdown=result.markdown,
            page_count=result.page_count,
            metadata={"engine": "paddleocr", **dict(result.metadata)},
        )

    async def health_check(self) -> bool:
        token = self._secrets.resolve(self._config.secret_ref)
        return bool(token and len(token) > 8)

    @property
    def capabilities(self) -> set[str]:
        return {"pdf", "image"}

    @property
    def strategy_name(self) -> str:
        return PADDLEOCR_PLUGIN_ID


class PaddleOcrPlugin(ParserPlugin):
    """Singleton; the polling handler is registered lazily on first
    ``build()`` call so it shares the process-wide scheduler."""

    def __init__(self, scheduler: PollingScheduler) -> None:
        self._scheduler = scheduler
        self._handler_registered = False

    @property
    def descriptor(self) -> ParserPluginDescriptor:
        return _DESCRIPTOR

    def build(
        self,
        config: ParserPluginConfig,
        secret_resolver: SecretResolver,
    ) -> ParserBackend:
        if not self._handler_registered:
            try:
                self._scheduler.register(PaddleOcrPollingHandler())
            except ValueError:
                # Already registered earlier — fine.
                pass
            self._handler_registered = True
        return _PaddleOcrBackend(
            config=config, secret_resolver=secret_resolver, scheduler=self._scheduler
        )
