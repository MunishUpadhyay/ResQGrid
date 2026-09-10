import pytest
import re
from django.contrib.auth.models import User
from apps.signals.models import Signal
from apps.tenants.models import Tenant


@pytest.fixture
def tenant(db):
    tenant_obj, _ = Tenant.objects.get_or_create(
        name="Persistence Test Tenant",
        defaults={"api_key_hash": Tenant.hash_api_key("persist_key")}
    )
    return tenant_obj


@pytest.mark.django_db
def test_full_user_data_and_report_persistence_lifecycle(client, tenant, mailoutbox):
    # TEST 1: Register citizen
    reg_resp = client.post("/citizen/register/", {
        "email": "persist_citizen@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!"
    })
    assert reg_resp.status_code == 302
    assert reg_resp.url == "/profile/"

    # TEST 2: Create first report belonging to citizen
    user = User.objects.get(email="persist_citizen@example.com")
    sig1 = Signal.objects.create(
        tenant=tenant,
        raw_text="First persisted civic report",
        preferred_language="english",
        user=user
    )
    assert sig1.user == user

    # TEST 3 & 4: Profile fetch verifies user data and first report
    profile_resp = client.get("/profile/")
    assert profile_resp.status_code == 200
    assert "PRAH-" in profile_resp.content.decode()

    # TEST 5: Logout
    client.logout()

    # TEST 6: Log back in
    login_resp = client.post("/citizen/login/", {
        "email": "persist_citizen@example.com",
        "password": "Password123!"
    })
    assert login_resp.status_code == 302
    assert login_resp.url == "/profile/"

    # TEST 7: Verify report still belongs to SAME user
    sig1.refresh_from_db()
    assert sig1.user == user
    assert Signal.objects.filter(user=user).count() == 1

    # TEST 8: Create second report after re-login
    sig2 = Signal.objects.create(
        tenant=tenant,
        raw_text="Second persisted civic report after re-login",
        preferred_language="english",
        user=user
    )

    # Verify both reports belong to user without duplication or loss
    user_signals = Signal.objects.filter(user=user).order_by("created_at")
    assert user_signals.count() == 2
    assert user_signals[0].id == sig1.id
    assert user_signals[1].id == sig2.id

    # TEST 9: Password reset does NOT alter user identity or report ownership
    client.logout()
    reset_req = client.post("/citizen/password-reset/", {"email": "persist_citizen@example.com"})
    assert reset_req.status_code == 302
    assert len(mailoutbox) == 1

    email_msg = mailoutbox[0]
    match = re.search(r"/citizen/password-reset-confirm/([^/]+)/([^/]+)/", email_msg.body)
    assert match is not None
    uidb64, token = match.group(1), match.group(2)

    reset_resp = client.post(f"/citizen/password-reset-confirm/{uidb64}/{token}/", {
        "password": "NewSecurePassword123!",
        "confirm_password": "NewSecurePassword123!"
    })
    assert reset_resp.status_code == 302
    assert reset_resp.url == "/citizen/password-reset/complete/"

    # Login with new password
    login_new_resp = client.post("/citizen/login/", {
        "email": "persist_citizen@example.com",
        "password": "NewSecurePassword123!"
    })
    assert login_new_resp.status_code == 302
    assert login_new_resp.url == "/profile/"

    # Verify user ID and report ownership remain unchanged
    re_login_user = User.objects.get(email="persist_citizen@example.com")
    assert re_login_user.pk == user.pk
    assert Signal.objects.filter(user=re_login_user).count() == 2
