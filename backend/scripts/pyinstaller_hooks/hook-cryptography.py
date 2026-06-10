# Fixed hook for cryptography 47+ where the top-level package is a namespace
# package with __file__=None, which crashes the upstream hook-cryptography.py
# at ``os.path.dirname(get_module_file_attribute('cryptography'))``.
#
# This hook replaces the upstream one by being loaded first via ``hookspath``.

import glob
import os
import pathlib

from PyInstaller import compat, isolated
from PyInstaller.utils.hooks import (
    collect_submodules,
    copy_metadata,
    is_module_satisfies,
    logger,
)

# get the package data so we can load the backends
datas = copy_metadata('cryptography')

# Add the backends as hidden imports
hiddenimports = collect_submodules('cryptography.hazmat.backends')

# Add the OpenSSL FFI binding modules as hidden imports
hiddenimports += collect_submodules('cryptography.hazmat.bindings.openssl') + ['_cffi_backend']

# Collect cffi extension binaries.  For namespace packages __file__ is None,
# so resolve the package directory via __path__ instead.
_binaries = []
try:
    import cryptography as _crypt
    _crypt_dir = next(iter(_crypt.__path__))
except (StopIteration, AttributeError):
    _crypt_dir = None

if _crypt_dir and os.path.isdir(_crypt_dir):
    for ext in compat.EXTENSION_SUFFIXES:
        for f in glob.glob(os.path.join(_crypt_dir, '*_cffi_*%s*' % ext)):
            _binaries.append((f, 'cryptography'))

binaries = _binaries

# --- OpenSSL 3 dynamic module collection (same as upstream) ---

try:
    @isolated.decorate
    def _check_cryptography_openssl3():
        from cryptography.hazmat.backends.openssl.backend import backend
        openssl_version = backend.openssl_version_number()
        if openssl_version < 0x30000000:
            return False, None

        try:
            import cryptography.hazmat.bindings._openssl as bindings_module
        except ImportError:
            import cryptography.hazmat.bindings._rust as bindings_module

        return True, str(bindings_module.__file__)

    uses_openssl3, bindings_module = _check_cryptography_openssl3()
except Exception:
    logger.warning(
        "hook-cryptography: failed to determine whether cryptography is using OpenSSL >= 3.0.0", exc_info=True
    )
    uses_openssl3, bindings_module = False, None

if uses_openssl3:
    openssl_lib = None
    if is_module_satisfies("PyInstaller >= 6.0"):
        from PyInstaller.depend import bindepend

        if compat.is_win:
            SSL_LIB_NAME = 'libssl-3-x64.dll' if compat.is_64bits else 'libssl-3.dll'
        elif compat.is_darwin:
            SSL_LIB_NAME = 'libssl.3.dylib'
        else:
            SSL_LIB_NAME = 'libssl.so.3'

        linked_libs = bindepend.get_imports(bindings_module)
        openssl_lib = [
            lib_fullpath for lib_name, lib_fullpath in linked_libs if os.path.basename(lib_name) == SSL_LIB_NAME
        ]
        openssl_lib = openssl_lib[0] if openssl_lib else None
    else:
        logger.warning(
            "hook-cryptography: full support for cryptography + OpenSSL >= 3.0.0 requires PyInstaller >= 6.0"
        )

    if openssl_lib:
        logger.info("hook-cryptography: cryptography uses dynamically-linked OpenSSL: %r", openssl_lib)

        openssl_lib_dir = pathlib.Path(openssl_lib).parent

        ossl_modules_dir = openssl_lib_dir / 'ossl-modules'

        if not ossl_modules_dir.is_dir() and openssl_lib_dir.name == 'bin':
            ossl_modules_dir = openssl_lib_dir.parent / 'lib' / 'ossl-modules'

        if not ossl_modules_dir.is_dir() and openssl_lib_dir == pathlib.Path('/lib'):
            ossl_modules_dir = pathlib.Path('/usr/lib/ossl-modules')

        if ossl_modules_dir.is_dir():
            logger.debug("hook-cryptography: collecting OpenSSL modules directory: %r", str(ossl_modules_dir))
            binaries.append((str(ossl_modules_dir), 'ossl-modules'))
    else:
        logger.info("hook-cryptography: cryptography does not seem to be using dynamically linked OpenSSL.")
