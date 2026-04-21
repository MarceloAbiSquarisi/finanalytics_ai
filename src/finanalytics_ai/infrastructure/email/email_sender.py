"""
finanalytics_ai.infrastructure.email.email_sender
──────────────────────────────────────────────────
Envio de e-mails transacionais via SMTP.

Se SMTP_HOST não estiver configurado, opera em modo "dry-run":
  - O token de reset é logado e retornado na resposta da API
  - Útil para desenvolvimento sem servidor de e-mail

Configuração no .env:
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=seu@gmail.com
  SMTP_PASSWORD=sua_senha_de_app   # senha de app do Gmail, não a senha normal
  SMTP_FROM=FinAnalytics AI <seu@gmail.com>
"""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

import structlog

logger = structlog.get_logger(__name__)


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._from = from_addr
        self._enabled = bool(host and user and password)

    @property
    def is_configured(self) -> bool:
        return self._enabled

    def send_reset_password(self, to_email: str, full_name: str, reset_url: str) -> bool:
        """
        Envia e-mail de redefinição de senha.
        Retorna True se enviado, False se em modo dry-run.
        """
        subject = "FinAnalytics AI — Redefinição de Senha"
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                    background:#0b1118;color:#e8eaf6;padding:32px;border-radius:12px;
                    border:1px solid #1e3a5f">
          <h2 style="color:#00c48c;margin-bottom:8px">FinAnalytics <span style="color:#F0B429">AI</span></h2>
          <hr style="border-color:#1e3a5f;margin:16px 0">
          <p style="margin-bottom:12px">Olá, <strong>{full_name}</strong>!</p>
          <p style="margin-bottom:20px;color:#8899aa">
            Recebemos uma solicitação para redefinir a senha da sua conta.
            Clique no botão abaixo para criar uma nova senha.
          </p>
          <a href="{reset_url}"
             style="display:inline-block;background:#00c48c;color:#000;
                    font-weight:700;padding:12px 28px;border-radius:7px;
                    text-decoration:none;letter-spacing:.5px">
            Redefinir Senha
          </a>
          <p style="margin-top:20px;font-size:12px;color:#556677">
            Este link expira em <strong>30 minutos</strong>.<br>
            Se você não solicitou a redefinição, ignore este e-mail.
          </p>
          <hr style="border-color:#1e3a5f;margin:20px 0">
          <p style="font-size:11px;color:#445566">
            FinAnalytics AI · Gestão de Investimentos
          </p>
        </div>
        """
        text = (
            f"Olá {full_name},\n\nRedefinição de senha:\n{reset_url}\n\n"
            f"Este link expira em 30 minutos."
        )

        if not self._enabled:
            logger.warning("email.dry_run", to=to_email, reset_url=reset_url)
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._from
            msg["To"] = to_email
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(self._user, self._password)
                smtp.sendmail(self._from, to_email, msg.as_string())

            logger.info("email.sent", to=to_email, subject=subject)
            return True
        except Exception as exc:
            logger.error("email.send_failed", to=to_email, error=str(exc))
            return False


_sender: EmailSender | None = None


def get_email_sender() -> EmailSender:
    global _sender
    if _sender is None:
        from finanalytics_ai.config import get_settings

        s = get_settings()
        _sender = EmailSender(
            host=s.smtp_host,
            port=s.smtp_port,
            user=s.smtp_user,
            password=s.smtp_password,
            from_addr=s.smtp_from,
        )
    return _sender
