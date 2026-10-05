"""EmailSender over SMTP with STARTTLS (Gmail with an app password)."""

import smtplib
from email.message import EmailMessage


class SmtpEmailSender:
    """Sends plain-text mail. The password is a secret: never log it."""

    def __init__(
        self, host: str, port: int, user: str, password: str, sender: str | None = None
    ) -> None:
        """Keep the SMTP server, the account and the From address (the account by default)."""
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._sender = sender or user

    def send(self, to: str, subject: str, body: str) -> None:
        """Send one message; raises on any SMTP or network error."""
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(self._user, self._password)
            smtp.send_message(message)
