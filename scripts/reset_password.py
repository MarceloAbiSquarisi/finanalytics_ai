# reset_password.py - usa SHA256 + bcrypt igual ao PasswordHasher do projeto
import hashlib
import subprocess

from passlib.context import CryptContext


def sha256_hex(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

ctx = CryptContext(schemes=['bcrypt'], deprecated='auto', bcrypt__rounds=12)

plain = 'admin123'
prepared = sha256_hex(plain)
h = ctx.hash(prepared)
print('Hash gerado:', h[:30])

cmd = [
    'docker','exec','finanalytics_postgres',
    'psql','-U','finanalytics','-d','finanalytics','-c',
    "UPDATE users SET hashed_password = '{}' WHERE email = 'marceloabisquarisi@gmail.com';".format(h)
]
r = subprocess.run(cmd, capture_output=True, text=True)
print('stdout:', r.stdout.strip())

cmd2 = [
    'docker','exec','finanalytics_postgres',
    'psql','-U','finanalytics','-d','finanalytics','-c',
    "SELECT LEFT(hashed_password,10) AS preview FROM users WHERE email = 'marceloabisquarisi@gmail.com';"
]
r2 = subprocess.run(cmd2, capture_output=True, text=True)
print('Preview:', r2.stdout.strip())
print('\nSenha: admin123')
