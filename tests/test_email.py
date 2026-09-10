import logging
import pytest
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from apps.signals.tasks import send_password_reset_email, BrevoTransientError, BrevoPermanentError

User = get_user_model()


@pytest.mark.django_db
class TestBrevoEmailConfigurationAndDelivery:
    """
    Test suite verifying Brevo HTTPS API configuration, Celery task execution,
    HTTP payload structure, failure handling, retry logic, secret masking,
    and password-reset security behavior.
    """

    def setup_method(self):
        self.client = APIClient()
        self.user_email = "citizen@example.com"
        self.user = User.objects.create_user(
            username="citizen1",
            email=self.user_email,
            password="oldpassword123"
        )
        self.reset_url = reverse("citizen_password_reset")
        self.subject = "Password Reset Request - ResQGrid"
        self.reset_link = "http://localhost:3000/reset-password?token=test-security-token-999&uid=uid-123"
        self.message = f"Please click the following link to reset your password: {self.reset_link}"

    @patch("apps.signals.tasks.send_password_reset_email.delay")
    def test_password_reset_request_queues_celery_task_and_does_not_call_brevo_directly(self, mock_delay):
        """
        Req 4, 5, 22, 23: Password-reset HTTP request enqueues Celery task asynchronously,
        does not call Brevo API directly, and preserves account-enumeration protection.
        """
        with patch("requests.post") as mock_http:
            response = self.client.post(self.reset_url, {"email": self.user_email})
            
            assert response.status_code == 302
            assert response.url == reverse("citizen_password_reset_done")
            assert mock_delay.called
            assert not mock_http.called

    @patch("apps.signals.tasks.send_password_reset_email.delay")
    def test_account_enumeration_protection_for_nonexistent_email(self, mock_delay):
        """
        Req 22, 23: Non-existent email returns identical redirect response without queuing email task.
        """
        response = self.client.post(self.reset_url, {"email": "nonexistent@example.com"})
        
        assert response.status_code == 302
        assert response.url == reverse("citizen_password_reset_done")
        assert not mock_delay.called

    @patch.dict("os.environ", {
        "BREVO_API_KEY": "test-brevo-key-12345",
        "BREVO_SENDER_EMAIL": "noreply@resqgrid.org",
        "BREVO_SENDER_NAME": "ResQGrid Alerts"
    })
    @patch("requests.post")
    def test_brevo_api_payload_and_headers_structure(self, mock_post):
        """
        Req 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16:
        Verifies HTTPS API URL, api-key header, Content-Type, sender name/email,
        recipient, subject, textContent, reset URL, and 2xx success handling.
        """
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"messageId": "<20260908.12345@brevo>"}
        mock_post.return_value = mock_response

        result = send_password_reset_email.run(
            _subject=self.subject,
            _message=self.message,
            recipient_email=self.user_email
        )

        assert isinstance(result, dict)
        assert result.get("status") == "sent"
        assert result.get("status_code") == 201
        assert mock_post.called

        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.brevo.com/v3/smtp/email"
        
        headers = kwargs.get("headers", {})
        assert headers.get("api-key") == "test-brevo-key-12345"
        assert headers.get("Content-Type") == "application/json"
        assert headers.get("Accept") == "application/json"

        json_data = kwargs.get("json", {})
        assert json_data.get("sender") == {
            "name": "ResQGrid Alerts",
            "email": "noreply@resqgrid.org"
        }
        assert json_data.get("to") == [{"email": self.user_email}]
        assert json_data.get("subject") == self.subject
        assert self.reset_link in json_data.get("textContent", "")

    @patch.dict("os.environ", {
        "BREVO_API_KEY": "test-brevo-key-12345",
        "BREVO_SENDER_EMAIL": "noreply@resqgrid.org",
        "BREVO_SENDER_NAME": "ResQGrid"
    })
    @patch("requests.post")
    def test_transient_5xx_server_error_raises_transient_error_for_celery_retry(self, mock_post):
        """
        Req 18: 5xx server error raises BrevoTransientError triggering Celery retry.
        """
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        with pytest.raises(Exception) as exc_info:
            send_password_reset_email.run(
                _subject=self.subject,
                _message=self.message,
                recipient_email=self.user_email
            )
        assert "BrevoTransientError" in str(type(exc_info.value).__name__) or "Retry" in str(type(exc_info.value).__name__)

    @patch.dict("os.environ", {
        "BREVO_API_KEY": "test-brevo-key-12345",
        "BREVO_SENDER_EMAIL": "noreply@resqgrid.org",
        "BREVO_SENDER_NAME": "ResQGrid"
    })
    @patch("requests.post")
    def test_network_connection_failure_raises_transient_error_for_celery_retry(self, mock_post):
        """
        Req 17: Transient network exception raises exception triggering Celery retry.
        """
        import requests
        mock_post.side_effect = requests.RequestException("Connection timeout")

        with pytest.raises(Exception) as exc_info:
            send_password_reset_email.run(
                _subject=self.subject,
                _message=self.message,
                recipient_email=self.user_email
            )
        assert "RequestException" in str(type(exc_info.value).__name__) or "Retry" in str(type(exc_info.value).__name__)

    @patch.dict("os.environ", {
        "BREVO_API_KEY": "test-brevo-key-12345",
        "BREVO_SENDER_EMAIL": "noreply@resqgrid.org",
        "BREVO_SENDER_NAME": "ResQGrid"
    })
    @patch("requests.post")
    def test_permanent_4xx_client_error_raises_permanent_error_no_infinite_retry(self, mock_post):
        """
        Req 19: Permanent 4xx client/auth error raises BrevoPermanentError to stop infinite retries.
        """
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Key not found"
        mock_post.return_value = mock_response

        with pytest.raises(BrevoPermanentError):
            send_password_reset_email.run(
                _subject=self.subject,
                _message=self.message,
                recipient_email=self.user_email
            )

    @patch.dict("os.environ", {
        "BREVO_API_KEY": "super-secret-brevo-api-key-99999",
        "BREVO_SENDER_EMAIL": "noreply@resqgrid.org",
        "BREVO_SENDER_NAME": "ResQGrid"
    })
    @patch("requests.post")
    def test_logs_never_contain_api_key_or_reset_token(self, mock_post, caplog):
        """
        Req 20, 21: Operational logs must NEVER contain BREVO_API_KEY or password reset tokens.
        """
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"messageId": "<msg-id-123>"}
        mock_post.return_value = mock_response

        secret_token = "secret-token-xyz-777"
        secret_api_key = "super-secret-brevo-api-key-99999"
        message_with_secret = f"Reset password using token: {secret_token}"

        with caplog.at_level(logging.DEBUG):
            send_password_reset_email.run(
                _subject=self.subject,
                _message=message_with_secret,
                recipient_email=self.user_email
            )

        log_text = caplog.text
        assert secret_api_key not in log_text
        assert secret_token not in log_text
