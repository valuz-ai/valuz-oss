"""Third-party plugins on a local deployment (ADR-034; docs task card 04).

Install, dev-link, enable / disable, uninstall, the asset server, per-user
storage and config, plugin-declared automations and logs. The renderer loads
the plugins; this module owns what is on disk and in ``valuz.db``.

* ``service.py``     ``AppPluginService`` — the one door (routes, the ``app_plugin_manager``
                     tool, overlay catalog installs)
* ``store.py``       ``installed.json`` (atomic writes, generation, long-poll wake-ups, safe mode)
* ``manifest.py``    ``valuz-plugin.json`` validation (JSON Schema + ``x-valuz-rules``)
* ``semver.py``      SemVer and npm-style ranges (``engines.valuz-plugin-api``)
* ``archive.py``     safe unzip, deterministic pack, https download
* ``permissions.py`` which API a plugin-originated request may reach
* ``automations.py`` manifest ``automations[]`` -> rows (``modules/automations/app_plugin_support``)
* ``logs.py``, ``models.py`` / ``datastore.py``, ``maintenance.py``, ``operations.py``
"""
