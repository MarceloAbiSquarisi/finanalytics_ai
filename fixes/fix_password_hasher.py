#!/usr/bin/env python3
"""
Fix PasswordHasher fallback (quando passlib não está instalada):
  - Adiciona salt aleatório por hash → hashes diferentes para mesma senha
  - Formato: sha256$<salt_hex>$<hmac_hex>
  - verify() extrai o salt do hash armazenado para comparação consistente
"""
import sys, pathlib, re

path = sys.argv[1] if len(sys.argv) > 1 else \
    r"src\finanalytics_ai\infrastructure\auth\password_hasher.py"

raw = pathlib.Path(path).read_bytes()
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        src = raw.decode(enc); break
    except UnicodeDecodeError:
        continue

# Adiciona import os se não existir
if "import os" not in src:
    src = src.replace("import hashlib\nimport hmac",
                      "import hashlib\nimport hmac\nimport os")

OLD_HASH = '''\
        salt = "finanalytics_dev_salt_not_for_prod"
        return "sha256$" + hashlib.sha256(f"{salt}{prepared}".encode()).hexdigest()'''

NEW_HASH = '''\
        salt = os.urandom(16).hex()
        digest = hmac.new(salt.encode(), prepared.encode(), hashlib.sha256).hexdigest()
        return f"sha256${salt}${digest}"'''

OLD_VERIFY = '''\
        expected = self.hash(plain_password)
        return hmac.compare_digest(expected.encode(), hashed_password.encode())'''

NEW_VERIFY = '''\
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False
        _, salt, stored_digest = parts
        digest = hmac.new(salt.encode(), prepared.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, stored_digest)'''

ok = True
if OLD_HASH in src:
    src = src.replace(OLD_HASH, NEW_HASH)
    print("[OK] hash(): salt aleatório adicionado")
else:
    print("[FAIL] hash() fallback não encontrado"); ok = False

if OLD_VERIFY in src:
    src = src.replace(OLD_VERIFY, NEW_VERIFY)
    print("[OK] verify(): extrai salt do hash armazenado")
else:
    print("[FAIL] verify() fallback não encontrado"); ok = False

if ok:
    pathlib.Path(path).write_text(src, encoding="utf-8")
    print(f"[OK] {path} corrigido")
