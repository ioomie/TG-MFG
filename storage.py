"""Opt-in local snapshots and OS-protected authorization; no browser secrets."""
import contextlib
import ctypes
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


class StorageError(Exception):
    pass


def data_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/TG-MFG/data'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'TG-MFG/data'
    return None


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    try:
        with temporary.open('wb') as file:
            if os.name != 'nt':
                os.fchmod(file.fileno(), 0o600)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def windows_protect(data, decrypt=False):
    # Current-user DPAPI only; never LOCAL_MACHINE, never interactive prompts.
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    method = library.CryptUnprotectData if decrypt else library.CryptProtectData
    method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    method.restype = wintypes.BOOL
    free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise StorageError('Windows could not protect or read the saved login. Sign in again.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free(output.data)


class MacKeychain:
    """Use Security.framework directly. Fail rather than open authorization UI."""
    def __init__(self, account):
        self.account = account
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        p = ctypes.c_void_p
        signatures = {
            'CFStringCreateWithCString': ([p, ctypes.c_char_p, ctypes.c_uint32], p),
            'CFDataCreate': ([p, p, ctypes.c_long], p),
            'CFDataGetLength': ([p], ctypes.c_long),
            'CFGetTypeID': ([p], ctypes.c_ulong), 'CFDataGetTypeID': ([], ctypes.c_ulong), 'CFDataGetBytePtr': ([p], p),
            'CFDictionaryCreateMutable': ([p, ctypes.c_long, p, p], p),
            'CFDictionarySetValue': ([p, p, p], None), 'CFRelease': ([p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.cf, name)
            fn.argtypes, fn.restype = args, result
        for name, args in {'SecItemCopyMatching':[p, ctypes.POINTER(p)],
                           'SecItemAdd':[p, p], 'SecItemUpdate':[p, p], 'SecItemDelete':[p]}.items():
            fn = getattr(self.sec, name)
            fn.argtypes, fn.restype = args, ctypes.c_int32

    def constant(self, name):
        return ctypes.c_void_p.in_dll(self.sec if name.startswith('kSec') else self.cf, name).value

    @contextlib.contextmanager
    def dictionary(self, values):
        keys = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, 'kCFTypeDictionaryKeyCallBacks'))
        vals = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, 'kCFTypeDictionaryValueCallBacks'))
        result = self.cf.CFDictionaryCreateMutable(None, 0, keys, vals)
        owned = []
        try:
            for key, value in values.items():
                if isinstance(value, str) and not value.startswith(('kSec', 'kCF')):
                    pointer = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
                    owned.append(pointer)
                elif isinstance(value, bytes):
                    pointer = self.cf.CFDataCreate(None, value, len(value))
                    owned.append(pointer)
                else:
                    pointer = self.constant(value)
                self.cf.CFDictionarySetValue(result, self.constant(key), pointer)
            yield result
        finally:
            self.cf.CFRelease(result)
            for pointer in owned:
                self.cf.CFRelease(pointer)

    def query(self):
        return {'kSecClass':'kSecClassGenericPassword', 'kSecAttrService':'TG-MFG authorization',
                'kSecAttrAccount':self.account, 'kSecUseAuthenticationUI':'kSecUseAuthenticationUIFail'}

    def read(self):
        output = ctypes.c_void_p()
        with self.dictionary({**self.query(), 'kSecReturnData':'kCFBooleanTrue'}) as query:
            code = self.sec.SecItemCopyMatching(query, ctypes.byref(output))
        if code == -25300:  # errSecItemNotFound
            return None
        if code != 0:
            raise StorageError(f'macOS Keychain is unavailable (status {code}). Unlock the login Keychain and try again, or sign in again.')
        try:
            if not output or self.cf.CFGetTypeID(output) != self.cf.CFDataGetTypeID():
                raise StorageError('The macOS Keychain record has an invalid format. Clear the saved login and sign in again.')
            return ctypes.string_at(self.cf.CFDataGetBytePtr(output), self.cf.CFDataGetLength(output))
        finally:
            self.cf.CFRelease(output)

    def write(self, data):
        with self.dictionary({**self.query(), 'kSecValueData':data}) as query:
            code = self.sec.SecItemAdd(query, None)
        if code == -25299:  # errSecDuplicateItem
            with self.dictionary(self.query()) as query, self.dictionary({'kSecValueData':data}) as change:
                code = self.sec.SecItemUpdate(query, change)
        if code != 0:
            raise StorageError('macOS Keychain could not save the login. The current session remains usable; sign in again after restarting.')

    def delete(self):
        with self.dictionary(self.query()) as query:
            code = self.sec.SecItemDelete(query)
        if code not in (0, -25300):
            raise StorageError('macOS Keychain could not clear the saved login. Unlock it and try again.')


class LoginVault:
    def __init__(self, root):
        self.root = root
        self.mac = MacKeychain(hashlib.sha256(str(root.resolve()).encode()).hexdigest()) if sys.platform == 'darwin' else None

    def read(self):
        if self.mac:
            data = self.mac.read()
        elif sys.platform == 'win32':
            path = self.root / 'login.bin'
            data = windows_protect(path.read_bytes(), True) if path.exists() else None
        else:
            raise StorageError('Saving login is supported only on macOS and Windows.')
        return json.loads(data) if data else None

    def write(self, record):
        data = json.dumps(record).encode()
        if self.mac:
            self.mac.write(data)
        elif sys.platform == 'win32':
            atomic_write(self.root / 'login.bin', windows_protect(data))
        else:
            raise StorageError('Saving login is supported only on macOS and Windows.')

    def delete(self):
        if self.mac:
            self.mac.delete()
        elif sys.platform == 'win32':
            (self.root / 'login.bin').unlink(missing_ok=True)


class LocalStore:
    def __init__(self, root=None, vault=None):
        self.root = Path(root) if root is not None else data_directory()
        self.vault = vault
        self.preferences = {'save_messages':False, 'retain_login':False, 'last_account':''}
        if self.root and (self.root / 'preferences.json').exists():
            try:
                record = json.loads((self.root / 'preferences.json').read_text())
                for key in ('save_messages', 'retain_login'):
                    self.preferences[key] = record.get(key) is True
                self.preferences['last_account'] = str(record.get('last_account', ''))
            except (ValueError, OSError):
                raise StorageError('Local storage settings could not be read. Check the data directory.') from None

    @property
    def supported(self):
        return self.root is not None

    def require(self):
        if not self.supported:
            raise StorageError('Local storage is supported only on macOS and Windows.')

    def login_vault(self):
        self.require()
        if self.vault is None:
            self.vault = LoginVault(self.root)
        return self.vault

    def settings(self, **changes):
        self.require()
        prefs = {**self.preferences, **changes}
        atomic_write(self.root / 'preferences.json', json.dumps(prefs).encode())
        self.preferences = prefs

    @contextlib.contextmanager
    def database(self):
        self.require()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / 'messages.sqlite3'
        db = sqlite3.connect(path)
        try:
            if os.name != 'nt':
                path.chmod(0o600)
            db.execute('PRAGMA secure_delete=ON')
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (account TEXT, channel TEXT, payload TEXT, PRIMARY KEY(account, channel))')
            with db:
                yield db
        finally:
            db.close()

    def snapshots(self, account):
        if not self.root or not (self.root / 'messages.sqlite3').exists() or not account:
            return []
        with self.database() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM snapshots WHERE account=?', (account,))]

    def save(self, account, snapshot):
        with self.database() as db:
            db.execute('INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)',
                       (account, snapshot['scan']['channel_id'], json.dumps(snapshot, ensure_ascii=False)))
        self.settings(last_account=account)

    def clear_messages(self):
        if self.root:
            # Close connections first; this store never keeps an open DB handle.
            for suffix in ('', '-journal', '-wal', '-shm'):
                (self.root / ('messages.sqlite3' + suffix)).unlink(missing_ok=True)
