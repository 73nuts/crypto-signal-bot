"""
Email sender module.
Sends email notifications with support for multiple recipients and SMTP configs.
"""

import logging
import smtplib
import socket
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


class EmailSender:
    """Email sender - supports multiple recipients and SMTP configs."""

    def __init__(self, email_config):
        """
        Args:
            email_config: Email config dict
        """
        self.email_config = email_config
        self.logger = logging.getLogger(__name__)

    def send(self, subject, message):
        """Send email to multiple recipients.

        Args:
            subject: Email subject
            message: Email body

        Returns:
            bool: Whether at least one email was sent successfully
        """
        try:
            if not self.email_config.get('enabled', False):
                self.logger.info("Email sending disabled")
                return False

            success_count = 0
            total_count = 0

            # Handle multiple recipient config
            recipients = self.email_config.get('recipients', [])

            # Fall back to legacy single-recipient config for backward compatibility
            if not recipients:
                recipients = [{
                    'email': self.email_config.get('to_email'),
                    'enabled': True
                }]

            for recipient in recipients:
                if not recipient.get('enabled', True):
                    continue

                total_count += 1
                recipient_email = recipient.get('email')

                if not recipient_email:
                    self.logger.warning("Recipient email address is empty, skipping")
                    continue

                # Use recipient-specific config, fall back to global config
                smtp_server = recipient.get('smtp_server', self.email_config.get('smtp_server'))

                # Support multiple SMTP ports (backward compatible)
                smtp_ports = recipient.get('smtp_ports', self.email_config.get('smtp_ports'))
                if smtp_ports is None:
                    # Fall back to single-port config
                    smtp_port = recipient.get('smtp_port', self.email_config.get('smtp_port'))
                    smtp_ports = [smtp_port] if smtp_port else [587]
                elif not isinstance(smtp_ports, list):
                    smtp_ports = [smtp_ports]

                sender_username = recipient.get('username', self.email_config.get('username'))
                sender_password = recipient.get('password', self.email_config.get('password'))

                if self._send_to_recipient(
                    recipient_email,
                    subject,
                    message,
                    smtp_server,
                    smtp_ports,
                    sender_username,
                    sender_password
                ):
                    success_count += 1

            self.logger.info(f"Email send complete: {success_count}/{total_count} succeeded")
            return success_count > 0

        except (smtplib.SMTPException, ssl.SSLError, socket.error, OSError) as e:
            self.logger.error(f"Email send failed: {e}")
            return False

    def _send_to_recipient(self, recipient_email, subject, message,
                          smtp_server, smtp_ports, sender_username, sender_password):
        """Send email to a single recipient with multi-port failover.

        Args:
            recipient_email: Recipient email address
            subject: Email subject
            message: Email body
            smtp_server: SMTP server address
            smtp_ports: List of SMTP ports to try in order
            sender_username: Sender username
            sender_password: Sender password

        Returns:
            bool: Whether send succeeded
        """
        for port in smtp_ports:
            try:
                self._send_via_port(
                    recipient_email, subject, message,
                    smtp_server, port, sender_username, sender_password
                )
                self.logger.info(f"Email sent to {recipient_email} (port {port})")
                return True
            except (smtplib.SMTPException, ssl.SSLError, socket.error, OSError) as e:
                self.logger.warning(f"Port {port} failed: {e}")
                continue

        self.logger.error(f"All ports {smtp_ports} failed, cannot send to {recipient_email}")
        return False

    def _send_via_port(self, recipient_email, subject, message,
                      smtp_server, port, sender_username, sender_password):
        """Send email via specified port.

        Args:
            recipient_email: Recipient email address
            subject: Email subject
            message: Email body
            smtp_server: SMTP server address
            port: SMTP port
            sender_username: Sender username
            sender_password: Sender password

        Raises:
            Exception: On send failure
        """
        msg = MIMEMultipart()
        msg['From'] = sender_username
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(message, 'plain', 'utf-8'))

        context = ssl.create_default_context()

        if port == 465:
            # SSL connection
            with smtplib.SMTP_SSL(smtp_server, port, context=context, timeout=30) as server:
                server.login(sender_username, sender_password)
                server.send_message(msg)
        else:
            # STARTTLS connection
            with smtplib.SMTP(smtp_server, port, timeout=30) as server:
                server.starttls(context=context)
                server.login(sender_username, sender_password)
                server.send_message(msg)
