#!/usr/bin/env python3
"""
Fix JWTHandler fallback: assina com HMAC-SHA256 e verifica na decodificação.

Problema:
  _encode_fallback() assina com "NOSIG" — sem assinatura real.
  _decode_fallback() não verifica assinatura — aceita qualquer token.
  Resultado: test_tampered_token_raises e test_wrong_secret_raises não falham.

Fix:
  _encode_fallback(): HMAC-SHA256(header.payload, secret_key) como assinatura.
  _decode_fallback(): verifica HMAC antes de aceitar o token.
"""
import sys, pathlib

path = sys.argv[1] if len(sys.argv) > 1 else \
    r"src\finanalytics_ai\infrastructure\auth\jwt_handler.py"

raw = pathlib.Path(path).read_bytes()
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        src = raw.decode(enc); break
    except UnicodeDecodeError:
        continue

OLD_ENCODE = '''\
    def _encode_fallback(self, claims: dict[str, Any]) -> str:
        """Base64url simples — NÃO use em produção."""
        import base64, json
        header  = base64.urlsafe_b64encode(b\'{"alg":"none"}\').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps(claims).encode()).rstrip(b"=").decode()
        return f"{header}.{payload}.NOSIG"'''

NEW_ENCODE = '''\
    def _encode_fallback(self, claims: dict[str, Any]) -> str:
        """HMAC-SHA256 fallback — funcional para testes sem python-jose/PyJWT."""
        import base64, json, hashlib, hmac as _hmac
        header  = base64.urlsafe_b64encode(b\'{"alg":"HS256"}\').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
        msg = f"{header}.{payload}".encode()
        sig = _hmac.new(self.secret_key.encode(), msg, hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        return f"{header}.{payload}.{sig_b64}"'''

OLD_DECODE = '''\
    def _decode_fallback(self, token: str) -> TokenPayload:
        import base64, json, time
        try:
            _, payload_b64, _ = token.split(".")
            pad     = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pad))
            if payload.get("exp", 0) < time.time():
                raise TokenExpiredError()
            return TokenPayload(
                sub=payload["sub"], email=payload["email"],
                role=payload.get("role","user"), exp=payload["exp"],
                token_type=payload.get("token_type","access"),
                jti=payload.get("jti",""),
            )
        except (TokenExpiredError, TokenInvalidError):
            raise
        except Exception as exc:
            raise TokenInvalidError(str(exc))'''

NEW_DECODE = '''\
    def _decode_fallback(self, token: str) -> TokenPayload:
        import base64, json, time, hashlib, hmac as _hmac
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise TokenInvalidError("Formato inválido.")
            header_b64, payload_b64, sig_b64 = parts
            # Verifica assinatura HMAC-SHA256
            msg      = f"{header_b64}.{payload_b64}".encode()
            expected = _hmac.new(self.secret_key.encode(), msg, hashlib.sha256).digest()
            expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
            if not _hmac.compare_digest(sig_b64, expected_b64):
                raise TokenInvalidError("Assinatura inválida.")
            pad     = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pad))
            if payload.get("exp", 0) < time.time():
                raise TokenExpiredError()
            return TokenPayload(
                sub=payload["sub"], email=payload["email"],
                role=payload.get("role","user"), exp=payload["exp"],
                token_type=payload.get("token_type","access"),
                jti=payload.get("jti",""),
            )
        except (TokenExpiredError, TokenInvalidError):
            raise
        except Exception as exc:
            raise TokenInvalidError(str(exc))'''

ok = True
if OLD_ENCODE in src:
    src = src.replace(OLD_ENCODE, NEW_ENCODE)
    print("[OK] _encode_fallback: HMAC-SHA256 real")
else:
    print("[FAIL] _encode_fallback não encontrado")
    ok = False

if OLD_DECODE in src:
    src = src.replace(OLD_DECODE, NEW_DECODE)
    print("[OK] _decode_fallback: verifica assinatura")
else:
    print("[FAIL] _decode_fallback não encontrado")
    ok = False

if ok:
    pathlib.Path(path).write_text(src, encoding="utf-8")
    print(f"[OK] {path} corrigido")
