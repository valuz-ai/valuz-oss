"""``ParserPlugin`` descriptor + builder for MinerU.

The plugin maps to a ``ParserBackend`` whose ``parse`` enqueues a job
on the shared ``PollingScheduler`` and awaits completion. The handler
that does the actual HTTP work lives in ``handler.py``.
"""

from __future__ import annotations

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
    SplitPolicy,
)
from plugins.parser.mineru.handler import (
    MINERU_HANDLER_KIND,
    MineruPollingHandler,
)

# MinerU's hosted ``/api/v4/extract/task`` rejects PDFs >200 pages with
# ``"number of pages exceeds limit (200 pages)"``. Set the descriptor
# limit one below so the splitter pre-splits anything over 199 pages
# into 199-page parts that fit comfortably.
_MINERU_MAX_PAGES = 199

MINERU_PLUGIN_ID = "mineru"

_API_TOKEN_SETUP = SetupRequirement(
    id="mineru_credentials",
    label_zh="MinerU API Token",
    label_key="parser_mineru.setup.label",
    kind="credential",
    network_required=False,
    source="mineru.net",
    license_name="MinerU 商用条款",
    license_url="https://mineru.net/protocol",
)

_DESCRIPTOR = ParserPluginDescriptor(
    id=MINERU_PLUGIN_ID,
    name_zh="MinerU(云端,精准 VLM)",
    description_zh=(
        "MinerU 精准解析 API,使用最高精度 VLM 模型。支持 PDF / 图片 / "
        "Office / Excel / HTML;HTML 自动切换到 MinerU-HTML 子模型。"
        "上传文件到 MinerU 服务器进行解析,需用户配置 API Token。"
    ),
    name_key="parser_mineru.descriptor.name",
    description_key="parser_mineru.descriptor.description",
    i18n_namespace="parser_mineru",
    sort_weight=20,
    mode=ParserPluginMode.ASYNC_POLL,
    capabilities=(
        PluginCapability(
            kind="pdf",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_API_TOKEN_SETUP,
        ),
        PluginCapability(
            kind="image",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_API_TOKEN_SETUP,
        ),
        PluginCapability(
            kind="office",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_API_TOKEN_SETUP,
        ),
        PluginCapability(
            kind="spreadsheet",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_API_TOKEN_SETUP,
        ),
        PluginCapability(
            kind="web",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_API_TOKEN_SETUP,
        ),
    ),
    config_schema=(
        ConfigField(
            key="api_token",
            label_zh="API Token",
            label_key="parser_mineru.config.api_token.label",
            type="secret",
            required=True,
            help_zh="在 mineru.net 用户后台申请",
            help_key="parser_mineru.config.api_token.help",
            help_url="https://mineru.net/apiManage/token",
        ),
        ConfigField(
            key="enable_formula",
            label_zh="公式识别",
            label_key="parser_mineru.config.enable_formula.label",
            type="bool",
            default=True,
        ),
        ConfigField(
            key="enable_table",
            label_zh="表格识别",
            label_key="parser_mineru.config.enable_table.label",
            type="bool",
            default=True,
        ),
        ConfigField(
            key="language",
            label_zh="文档语言",
            label_key="parser_mineru.config.language.label",
            type="select",
            default="auto",
            options=(
                ("auto", "自动识别"),
                ("ch", "中文"),
                ("en", "English"),
            ),
            option_keys=(
                "parser_mineru.config.language.options.auto",
                "parser_mineru.config.language.options.ch",
                "parser_mineru.config.language.options.en",
            ),
        ),
    ),
    fallback_policy="allow_local_fallback",
    split_policy=SplitPolicy(max_pages=_MINERU_MAX_PAGES),
)


class _MineruBackend(ParserBackend):
    """One per (plugin_config, scheduler) — cheap to construct."""

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
            raise RuntimeError("MinerU API token is not configured")
        del options  # MinerU doesn't honour our generic ParseOptions today

        # PDFs over MinerU's 200-page cap get pre-split into 199-page
        # parts and parsed sequentially — keeps configured-cloud usage
        # instead of falling back to LightLocal just because the file
        # is large. ``split_and_parse_async`` is a no-op for in-limit
        # files (single ``await`` call, no temp files).
        from pathlib import Path

        from valuz_agent.modules.parser.splitter import split_and_parse_async

        async def _parse_one(path: Path) -> ParseResult:
            return await self._submit_one(path, token)

        return await split_and_parse_async(Path(file_path), _DESCRIPTOR.split_policy, _parse_one)

    async def _submit_one(self, file_path, token: str) -> ParseResult:  # type: ignore[no-untyped-def]
        """Submit a single file (or split part) to MinerU and await
        the polling scheduler. Used by ``parse`` after splitting."""
        payload = {
            "file_path": str(file_path),
            "token": token,
            "options": dict(self._config.options),
        }
        task_id = await self._scheduler.enqueue(MINERU_HANDLER_KIND, payload)
        result = await self._scheduler.await_task(task_id)
        return ParseResult(
            markdown=result.markdown,
            page_count=result.page_count,
            metadata={"engine": "mineru", **dict(result.metadata)},
        )

    async def health_check(self) -> bool:
        """Light validation — token presence + format. The plugin's
        ``test`` endpoint uses this; we deliberately avoid hitting the
        remote here so a single page-load doesn't burn quota."""
        token = self._secrets.resolve(self._config.secret_ref)
        return bool(token and len(token) > 8)

    @property
    def capabilities(self) -> set[str]:
        return {"pdf", "image", "office", "spreadsheet", "web"}

    @property
    def strategy_name(self) -> str:
        return MINERU_PLUGIN_ID


class MineruPlugin(ParserPlugin):
    """MinerU plugin singleton. Constructed with a reference to the
    process-wide ``PollingScheduler`` so submitted tasks share the same
    worker thread + backoff state."""

    def __init__(self, scheduler: PollingScheduler) -> None:
        self._scheduler = scheduler
        # Register our handler with the scheduler lazily — only when the
        # plugin is actually built do we know the token is present.
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
                self._scheduler.register(MineruPollingHandler())
            except ValueError:
                # Already registered by a previous build call in a
                # different code path — fine.
                pass
            self._handler_registered = True
        return _MineruBackend(
            config=config, secret_resolver=secret_resolver, scheduler=self._scheduler
        )
