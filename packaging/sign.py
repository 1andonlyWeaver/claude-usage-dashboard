"""
Sign release installers, so installed copies will install them (updates.py).

Run on the maintainer's PC only, in the conda env (it has `cryptography`). The private key
never goes into the repo or GitHub: someone who gets into the GitHub account still can't sign.

    python packaging/sign.py new-key
        Once. Makes the key pair, asks for a password for the private key, saves it to
        KEY_FILE and writes the public key into updates.py's SIGNING_KEY. Commit updates.py.
        Back up the key file and its password (a password manager is fine): without them,
        installed copies can't update themselves until everyone installs a new build by hand.

    python packaging/sign.py sign dist/ClaudeUsageDashboard-Setup-2.1.0.exe
        Each release, after trying the draft's installer: writes <installer>.sig beside it.
        Upload that to the draft (gh release upload 2.1.0 <installer>.sig), then publish.

    python packaging/sign.py verify dist/ClaudeUsageDashboard-Setup-2.1.0.exe
        Checks <installer>.sig the way the app will.

--key PATH uses another key file. CUD_SIGNING_KEY sets it too.
"""
import argparse
import base64
import getpass
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import updates  # noqa: E402  (after the path: run as a script, packaging/ is first on it)

UPDATES_PY = ROOT / "updates.py"
KEY_FILE = Path.home() / ".claude-usage-dashboard" / "update-signing-key.pem"
_KEY_LINE = re.compile(r'^SIGNING_KEY = "[^"\n]*"$', re.MULTILINE)
_INSTALLER = re.compile(r"ClaudeUsageDashboard-Setup-(\d+(?:\.\d+){0,3})\.exe")


def key_path(arg: str | None) -> Path:
    return Path(arg or os.environ.get("CUD_SIGNING_KEY") or KEY_FILE)


def public_key_b64(private) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def set_signing_key(public: str, path: Path = UPDATES_PY) -> None:
    """Write `public` into updates.py's SIGNING_KEY line."""
    text = path.read_text(encoding="utf-8")
    new, count = _KEY_LINE.subn(f'SIGNING_KEY = "{public}"', text)
    if count != 1:
        raise SystemExit(f"Couldn't find the SIGNING_KEY line in {path}")
    path.write_text(new, encoding="utf-8", newline="\n")  # LF, as .gitattributes keeps it; not Windows' CRLF


def new_key(path: Path, password: bytes) -> str:
    """Make a key pair, save the private key to `path` encrypted with `password`, return the public key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if path.exists():
        raise SystemExit(f"{path} already exists. Move it away first if you really mean to replace "
                         "the key: copies installed with the old one won't accept releases signed "
                         "with a new one.")
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(password))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as out:
        out.write(pem)
    return public_key_b64(private)


def load_key(path: Path, password: bytes):
    from cryptography.hazmat.primitives import serialization

    try:
        data = path.read_bytes()
    except OSError as ex:
        raise SystemExit(f"Couldn't read the key file {path}: {ex.strerror or ex}") from None
    try:
        return serialization.load_pem_private_key(data, password=password)
    except (ValueError, TypeError):
        raise SystemExit("Wrong password, or that isn't the signing key file.") from None


def release_number(installer: Path) -> str:
    match = _INSTALLER.fullmatch(installer.name)
    if not match:
        raise SystemExit(f"{installer.name} isn't named like a release installer "
                         "(ClaudeUsageDashboard-Setup-<version>.exe)")
    return match.group(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sign(installer: Path, private) -> Path:
    """Write <installer>.sig; refuse a key that isn't the one updates.py checks against."""
    if public_key_b64(private) != updates.SIGNING_KEY:
        raise SystemExit("That key doesn't match updates.SIGNING_KEY, so installed copies would "
                         "refuse its signature.")
    tag = release_number(installer)
    signature = private.sign(updates.signed_message(tag, sha256(installer)))
    out = installer.with_name(installer.name + ".sig")
    out.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    return out


def verify(installer: Path) -> bool:
    try:
        signature = installer.with_name(installer.name + ".sig").read_text(encoding="ascii")
    except (OSError, ValueError):
        return False
    return updates.verify(release_number(installer), sha256(installer), signature)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sign release installers for in-app updates.")
    parser.add_argument("--key", help=f"the private key file (default {KEY_FILE})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new-key", help="make the key pair and write the public key into updates.py")
    for name in ("sign", "verify"):
        commands.add_parser(name).add_argument("installer", type=Path)
    args = parser.parse_args(argv)
    path = key_path(args.key)

    if args.command == "new-key":
        password = getpass.getpass("Password for the new private key: ")
        if len(password) < 12:
            raise SystemExit("Use at least 12 characters.")
        if getpass.getpass("Again: ") != password:
            raise SystemExit("The passwords didn't match.")
        public = new_key(path, password.encode())
        set_signing_key(public)
        print(f"Saved the private key to {path}")
        print(f"Wrote the public key into {UPDATES_PY.name}: {public}")
        print("Now back up that file and its password, then commit updates.py.")
        return 0

    if not args.installer.is_file():
        raise SystemExit(f"No such file: {args.installer}")
    if args.command == "sign":
        private = load_key(path, getpass.getpass(f"Password for {path}: ").encode())
        out = sign(args.installer, private)
        if not verify(args.installer):
            raise SystemExit("The new signature didn't verify. Don't upload it.")
        print(f"Wrote {out}")
        print(f"Upload it to the draft: gh release upload {release_number(args.installer)} \"{out}\"")
        return 0

    ok = verify(args.installer)
    print("The signature checks out." if ok else "No valid signature for this installer.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
