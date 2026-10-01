"""An in-memory stand-in for the parts of winreg that autostart.py uses."""


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _missing():
    return FileNotFoundError(2, "The system cannot find the file specified")


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1
    REG_BINARY = 3

    def __init__(self):
        self.keys = {}  # (root, path) -> {value name: (data, kind)}

    def OpenKey(self, root, path, reserved=0, access=KEY_READ):
        if (root, path) not in self.keys:
            raise _missing()
        return FakeKey((root, path))

    def CreateKeyEx(self, root, path, reserved=0, access=KEY_READ):
        self.keys.setdefault((root, path), {})
        return FakeKey((root, path))

    def QueryValueEx(self, key, name):
        try:
            return self.keys[key.path][name]
        except KeyError:
            raise _missing() from None

    def SetValueEx(self, key, name, reserved, kind, data):
        self.keys[key.path][name] = (data, kind)

    def DeleteValue(self, key, name):
        try:
            del self.keys[key.path][name]
        except KeyError:
            raise _missing() from None

    # Helpers for tests
    def value(self, path, name):
        return self.keys.get((self.HKEY_CURRENT_USER, path), {}).get(name, (None, None))[0]

    def set(self, path, name, data, kind=REG_SZ):
        self.keys.setdefault((self.HKEY_CURRENT_USER, path), {})[name] = (data, kind)
