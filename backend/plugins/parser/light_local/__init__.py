"""ParserPlugin wrapper around the in-process ``LightLocalParser``.

PR-2: ``image`` is now ``needs_setup`` — the RapidOCR ONNX bundle must
be downloaded with explicit user authorization before image parsing is
available. The router's capability gate consults the setup-job marker
file on every parse and promotes ``image`` to ``ready`` once the
download is complete (without us having to mutate the descriptor — see
``ParserRouter._kind_is_ready``).

All other kinds (pdf / office / spreadsheet / web / text) remain
``ready`` — their backends ship as Python packages with no extra assets.
"""

from __future__ import annotations

from valuz_agent.integrations.parser_light_local import LightLocalParser
from valuz_agent.modules.parser.setup_jobs.rapidocr import RAPIDOCR_SETUP_ID
from valuz_agent.ports.parser_backend import ParserBackend
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ParserPlugin,
    ParserPluginConfig,
    ParserPluginDescriptor,
    ParserPluginMode,
    PluginCapability,
    SecretResolver,
    SetupRequirement,
)

LIGHT_LOCAL_PLUGIN_ID = "light_local"

_RAPIDOCR_SETUP = SetupRequirement(
    id=RAPIDOCR_SETUP_ID,
    label_zh="下载 OCR 模型(PP-OCRv5 ONNX)",
    label_key="parser_light_local.setup.label",
    kind="model_download",
    network_required=True,
    # Conservative declared size until ``RapidOcrSetupJob`` reports the
    # measured value via the HEAD pre-pass at start time. PP-OCRv5
    # mobile bundle is ~20MB; round up to 24MB for the dialog so we
    # don't under-promise.
    size_bytes=24 * 1024 * 1024,
    source="modelscope_official",
    license_name="Apache License 2.0",
    license_url="https://www.apache.org/licenses/LICENSE-2.0.txt",
)

_DESCRIPTOR = ParserPluginDescriptor(
    id=LIGHT_LOCAL_PLUGIN_ID,
    name_zh="本地解析",
    description_zh=(
        "进程内解析:PyMuPDF4LLM(PDF)、MarkItDown(Office/Excel)、"
        "html-to-markdown(HTML)、RapidOCR(图片)。除图片 OCR 模型需用户授权下载外,"
        "全部无需联网。"
    ),
    name_key="parser_light_local.descriptor.name",
    description_key="parser_light_local.descriptor.description",
    i18n_namespace="parser_light_local",
    # Always-available fallback — render first in the settings UI.
    sort_weight=10,
    mode=ParserPluginMode.SYNC,
    capabilities=(
        PluginCapability(kind="pdf", status=CapabilityStatus.READY),
        PluginCapability(kind="office", status=CapabilityStatus.READY),
        PluginCapability(kind="spreadsheet", status=CapabilityStatus.READY),
        PluginCapability(kind="web", status=CapabilityStatus.READY),
        PluginCapability(kind="text", status=CapabilityStatus.READY),
        # The router's capability gate consults the setup-job marker
        # via ``RapidOcrSetupJob.is_complete()`` and promotes to READY
        # at request time when the user has authorized the download.
        PluginCapability(
            kind="image",
            status=CapabilityStatus.NEEDS_SETUP,
            setup=_RAPIDOCR_SETUP,
            reason_zh="首次使用图片 OCR 需下载本地模型",
        ),
    ),
    config_schema=(),
    fallback_policy="strict",  # the local parser IS the fallback
)


class LightLocalPlugin(ParserPlugin):
    """Singleton: ``LightLocalParser`` is cheap to construct and stateless."""

    def __init__(self) -> None:
        self._backend: ParserBackend = LightLocalParser()

    @property
    def descriptor(self) -> ParserPluginDescriptor:
        return _DESCRIPTOR

    def build(
        self,
        config: ParserPluginConfig,
        secret_resolver: SecretResolver,
    ) -> ParserBackend:
        # config + secrets are ignored by the local parser; signature
        # matches the protocol so the router's call site is uniform.
        del config, secret_resolver
        return self._backend


def make_plugin(scheduler: object | None = None) -> LightLocalPlugin:
    """Entry-point factory. ``light_local`` is fully synchronous and
    does not use the polling scheduler — the ``scheduler`` parameter
    is accepted for signature uniformity with the cloud plugins."""
    del scheduler
    return LightLocalPlugin()
