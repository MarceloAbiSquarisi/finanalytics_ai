cls#!/usr/bin/env python3
"""
Fix JWTHandler: o fallback _encode/_decode usa alg:none sem HMAC.
Resultado: tokens adulterados e com secret errado são aceitos.

Fix: substituir o fallback por HMAC-SHA256 puro (sem dependência externa).
Isso torna o fallback seguro para testes e dev, mantendo a mesma interface.
"""
import sys, pathlib, re

path = sys.argv[1] if len(sys.argv) > 1 else r"src\finanalytics_ai\infrastructure\auth\jwt_handler.py"

raw = pathlib.Path(path).read_bytes()
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        src = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue

OLD_ENCODE = '''    def _encode_fallback(self, claims: dict[str, Any]) -> str:
        """Base64url simples — NÃO use em produção."""
        import base64, json
        header  = base64.urlsafe_b64encode(b\'{"alg":"none"}\').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps(claims).encode()).rstrip(b"=").decode()
        return f"{header}.{payload}.NOSIG"

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

NEW_ENCODE = '''    def _encode_fallback(self, claims: dict[str, Any]) -> str:
        """
        Fallback JWT usando HMAC-SHA256 puro (sem dependência externa).
        Formato: base64url(header).base64url(payload).base64url(hmac)
        Seguro para testes e desenvolvimento.
        """
        import base64, json, hmac, hashlib
        header  = base64.urlsafe_b64encode(b\'{"alg":"HS256","typ":"JWT"}\').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
        signing_input = f"{header}.{payload}".encode()
        sig = hmac.new(
            self.secret_key.encode(), signing_input, hashlib.sha256
        ).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        return f"{header}.{payload}.{sig_b64}"

    def _decode_fallback(self, token: str) -> TokenPayload:
        import base64, json, hmac, hashlib, time
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise TokenInvalidError("Formato JWT inválido.")
            header_b64, payload_b64, sig_b64 = parts

            # Valida assinatura HMAC-SHA256
            signing_input = f"{header_b64}.{payload_b64}".encode()
            expected_sig = hmac.new(
                self.secret_key.encode(), signing_input, hashlib.sha256
            ).digest()
            try:
                received_sig = base64.urlsafe_b64decode(
                    sig_b64 + "=" * (-len(sig_b64) % 4)
                )
            except Exception:
                raise TokenInvalidError("Assinatura malformada.")
            if not hmac.compare_digest(expected_sig, received_sig):
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

if OLD_ENCODE in src:
    pathlib.Path(path).write_text(src.replace(OLD_ENCODE, NEW_ENCODE), encoding="utf-8")
    print("[OK] JWTHandler fallback corrigido: HMAC-SHA256 com validação de assinatura")
else:
    # Tenta encontrar pela assinatura do método
    if "_decode_fallback" in src and "NOSIG" in src:
        # Substituição linha por linha mais robusta
        lines = src.splitlines(keepends=True)
        out = []
        i = 0
        in_encode = False
        in_decode = False
        indent = ""
        while i < len(lines):
            line = lines[i]
            if "_encode_fallback" in line and "def " in line:
                in_encode = True
                # Pula até o próximo método
                i += 1
                while i < len(lines):
                    if lines[i].strip().startswith("def ") and not lines[i].startswith(" " * 8):
                        break
                    i += 1
                # Insere o novo encode+decode
                out.append(NEW_ENCODE + "\n")
                continue
            elif "_decode_fallback" in line and "def " in line:
                in_decode = True
                i += 1
                while i < len(lines):
                    if lines[i].strip().startswith("def ") and not lines[i].startswith(" " * 12):
                        break
                    i += 1
                continue  # já foi inserido junto com encode
            else:
                out.append(line)
            i += 1
        pathlib.Path(path).write_text("".join(out), encoding="utf-8")
        print("[OK] JWTHandler fallback corrigido (método alternativo)")
    else:
        print(f"[INFO] _BACKEND provavelmente é 'jose' ou 'pyjwt' — testando se há problema real")
        # Se jose/pyjwt está instalado, o bug pode ser outro: decode sem options
        # Verifica se há `options={"verify_signature": False}` ou similar
        if "verify_signature" in src.lower() or "verify=False" in src:
            print("[WARN] Encontrado verify=False — isso seria o bug real")
        else:
            print("[INFO] JWT backend externo parece OK — problema pode ser no test setup")
            print("Conteúdo atual do fallback:")
            for line in src.splitlines():
                if "fallback" in line.lower() or "NOSIG" in line or "nosig" in line.lower():
                    print(f"  {line}")
