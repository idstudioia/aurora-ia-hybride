"""Windows Credential Manager (« coffre Windows ») for API keys — CLAUDE.md: no key in code, logs or Git.

Generic credentials of the current Windows user, through advapi32 (ctypes, no extra dependency).
The secret is stored as UTF-16-LE, like `cmdkey /generic:<cible> /user:aurora /pass:<clé>` does,
so a key saved from the command line is read the same way. Elsewhere than Windows the vault is
unavailable and callers fall back to the environment (config.anthropic_api_key).
Secrets are never logged: errors carry the Windows error code only.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

ANTHROPIC_TARGET = 'AURORA/anthropic_api_key'
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2     # this Windows user, survives reboots, not roamed
ERROR_NOT_FOUND = 1168


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
                ('Comment', wintypes.LPWSTR), ('LastWritten', wintypes.FILETIME),
                ('CredentialBlobSize', wintypes.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]


def available() -> bool:
    return sys.platform == 'win32'


def _advapi():
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    adv.CredWriteW.restype = wintypes.BOOL
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredDeleteW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    adv.CredFree.restype = None
    return adv


def read(target: str = ANTHROPIC_TARGET) -> str | None:
    """The stored secret, or None (absent, unreadable or not on Windows)."""
    if not available():
        return None
    try:
        adv = _advapi()
        found = ctypes.POINTER(_CREDENTIALW)()
        if not adv.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
            code = ctypes.get_last_error()
            if code != ERROR_NOT_FOUND:
                log.warning('Windows vault read failed (error %s)', code)
            return None
        try:
            cred = found.contents
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize) if cred.CredentialBlobSize else b''
        finally:
            adv.CredFree(found)
    except OSError:
        log.warning('Windows vault unavailable')
        return None
    text = blob.decode('utf-16-le', errors='ignore').strip('\x00').strip()
    return text or None


def write(secret: str, target: str = ANTHROPIC_TARGET, user: str = 'aurora') -> None:
    """Store (or replace) the secret. Raises OSError (French message, no secret in it)."""
    if not available():
        raise OSError('Le coffre Windows n’est disponible que sous Windows.')
    blob = str(secret).encode('utf-16-le')
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _CREDENTIALW()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = 'AURORA — clé API (enregistrée depuis Paramètres)'
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = user
    if not _advapi().CredWriteW(ctypes.byref(cred), 0):
        raise OSError(f'Écriture dans le coffre Windows impossible (erreur {ctypes.get_last_error()}).')


def delete(target: str = ANTHROPIC_TARGET) -> bool:
    """True if a secret was removed."""
    if not available():
        return False
    if _advapi().CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    code = ctypes.get_last_error()
    if code != ERROR_NOT_FOUND:
        raise OSError(f'Suppression dans le coffre Windows impossible (erreur {code}).')
    return False
