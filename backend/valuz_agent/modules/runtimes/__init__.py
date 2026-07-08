"""Runtime installation module.

On-demand installation of runtime binaries the desktop bundle no longer
ships (today: the codex CLI). The runtime *registry* (display names,
protocols, availability probes) lives in ``adapters/runtime_registry``;
this module owns only the download/install side.
"""
