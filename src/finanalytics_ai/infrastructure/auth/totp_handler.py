"""
finanalytics_ai.infrastructure.auth.totp_handler
─────────────────────────────────────────────────
TOTP (Time-based One-Time Password) — RFC 6238.
Compatível com Google Authenticator, Authy, 1Password etc.

Design:
  - Janela de ±1 intervalo (30s) para tolerar dessincronismo de relógio
  - QR code gerado como base64 PNG (embutível em <img src="data:...">)
  - Secret armazenado criptografado na coluna totp_secret (Base32)
"""
from __future__ import annotations

import base64
import io

import structlog

log = structlog.get_logger(__name__)

try:
    import pyotp
    _PYOTP_AVAILABLE = True
except ImportError:
    _PYOTP_AVAILABLE = False

try:
    import qrcode
    _QRCODE_AVAILABLE = True
except ImportError:
    _QRCODE_AVAILABLE = False


class TOTPHandler:
    """Gera e valida códigos TOTP."""

    ISSUER = "FinAnalytics AI"
    WINDOW = 1  # ±1 período de 30s

    def generate_secret(self) -> str:
        """Gera novo secret Base32 para o usuário."""
        if not _PYOTP_AVAILABLE:
            raise RuntimeError("pyotp não instalado. pip install pyotp")
        return pyotp.random_base32()

    def get_provisioning_uri(self, secret: str, email: str) -> str:
        """Retorna URI otpauth:// para gerar QR code."""
        if not _PYOTP_AVAILABLE:
            raise RuntimeError("pyotp não instalado.")
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=email, issuer_name=self.ISSUER)

    def get_qr_base64(self, secret: str, email: str) -> str:
        """Retorna QR code como string base64 PNG."""
        uri = self.get_provisioning_uri(secret, email)
        if not _QRCODE_AVAILABLE:
            # Fallback: retorna apenas a URI para o cliente gerar o QR
            return ""
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def verify(self, secret: str, code: str) -> bool:
        """Verifica código TOTP com janela de tolerância."""
        if not _PYOTP_AVAILABLE:
            raise RuntimeError("pyotp não instalado.")
        if not secret or not code:
            return False
        totp = pyotp.TOTP(secret)
        return totp.verify(code.strip(), valid_window=self.WINDOW)


_handler: TOTPHandler | None = None


def get_totp_handler() -> TOTPHandler:
    global _handler
    if _handler is None:
        _handler = TOTPHandler()
    return _handler
