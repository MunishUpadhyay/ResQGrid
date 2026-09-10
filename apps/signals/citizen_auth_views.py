import logging
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.db import IntegrityError

logger = logging.getLogger(__name__)

def citizen_register(request):
    """
    GET /citizen/register/ - Renders registration form.
    POST /citizen/register/ - Registers citizen and logs them in.
    """
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("/coordinator/dashboard/")
        return redirect("/profile/")

    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if not email or not password or not confirm_password:
            error = "All fields are required. / सभी फ़ील्ड भरना आवश्यक है।"
        elif password != confirm_password:
            error = "Passwords do not match. / पासवर्ड मेल नहीं खाते हैं।"
        elif len(password) < 6:
            error = "Password must be at least 6 characters. / पासवर्ड कम से कम 6 अक्षरों का होना चाहिए।"
        else:
            try:
                # Check for existing email in username (since username=email for citizens)
                if User.objects.filter(username=email).exists():
                    error = "An account with this email already exists. / इस ईमेल के साथ एक खाता पहले से मौजूद है।"
                else:
                    # Create native Django User
                    user = User.objects.create_user(
                        username=email,
                        email=email,
                        password=password,
                        is_staff=False
                    )
                    login(request, user)
                    logger.info("Citizen registered successfully: %s", email)
                    return redirect("/profile/")
            except IntegrityError:
                error = "An error occurred. Please try again. / एक त्रुटि हुई। कृपया पुनः प्रयास करें।"

    return render(request, "citizen_register.html", {"error": error})

def citizen_login(request):
    """
    GET /citizen/login/ - Renders citizen login form.
    POST /citizen/login/ - Authenticates and logs citizen in.
    """
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("/coordinator/dashboard/")
        return redirect("/profile/")

    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")

        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            logger.info("Citizen logged in: %s", email)
            if user.is_staff:
                return redirect("/coordinator/dashboard/")
            return redirect("/profile/")
        else:
            error = "Invalid email or password. / अमान्य ईमेल या पासवर्ड।"

    return render(request, "citizen_login.html", {"error": error})

def citizen_logout_view(request):
    """
    GET /citizen/logout/ - Logs out citizen and redirects to home page.
    """
    logout(request)
    return redirect("/")


# --- Citizen Password Reset & Account Recovery Views ---

from django.urls import reverse
from django.core.mail import send_mail
from django.conf import settings
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator


def citizen_password_reset_request(request):
    """
    GET /citizen/password-reset/ - Renders password reset email request form.
    POST /citizen/password-reset/ - Sends reset link to valid user email without leaking email existence.
    """
    if request.user.is_authenticated:
        return redirect("/profile/")

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        if email:
            # Enumerate-safe lookup: search for matching active citizen users
            users = User.objects.filter(username=email, is_active=True)
            if not users.exists():
                users = User.objects.filter(email=email, is_active=True)

            for user in users:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                reset_url = request.build_absolute_uri(
                    reverse("citizen_password_reset_confirm", kwargs={"uidb64": uid, "token": token})
                )
                
                subject = "Prahari — Password Reset Request / पासवर्ड रीसेट अनुरोध"
                message = (
                    f"Hello / नमस्ते,\n\n"
                    f"A password reset request was received for your Prahari account ({email}).\n"
                    f"आपकी प्रहरी खाता साख के लिए पासवर्ड रीसेट अनुरोध प्राप्त हुआ था।\n\n"
                    f"Click the link below to set a new password:\n"
                    f"नया पासवर्ड सेट करने के लिए नीचे दिए गए लिंक पर क्लिक करें:\n"
                    f"{reset_url}\n\n"
                    f"If you did not request this change, please ignore this email.\n"
                    f"यदि आपने यह अनुरोध नहीं किया था, तो कृपया इस ईमेल पर ध्यान न दें।\n\n"
                    f"-- Prahari System / प्रहरी टीम"
                )
                try:
                    from apps.signals.tasks import send_password_reset_email
                    target_addr = user.email or email
                    send_password_reset_email.delay(subject, message, target_addr)
                    logger.info("[PasswordReset] Enqueued reset email task for citizen: %s", email)
                except Exception as e:
                    logger.error("[PasswordReset] Email task enqueue failed for %s: %s", email, e)


        # Always redirect to done page to prevent email enumeration attack
        return redirect("citizen_password_reset_done")

    return render(request, "citizen_password_reset.html")


def citizen_password_reset_done(request):
    """
    GET /citizen/password-reset/done/ - Displays confirmation that instructions were sent.
    """
    return render(request, "citizen_password_reset_done.html")


def citizen_password_reset_confirm(request, uidb64, token):
    """
    GET /citizen/password-reset-confirm/<uidb64>/<token>/ - Renders password reset confirmation form.
    POST /citizen/password-reset-confirm/<uidb64>/<token>/ - Validates token and updates password.
    """
    user = None
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    valid = (user is not None) and default_token_generator.check_token(user, token)
    error = None

    if request.method == "POST":
        if not valid:
            error = "This password reset link is invalid or has expired. / यह पासवर्ड रीसेट लिंक अमान्य है या इसकी समयावधि समाप्त हो चुकी है।"
        else:
            password = request.POST.get("password", "")
            confirm_password = request.POST.get("confirm_password", "")

            if not password or not confirm_password:
                error = "All fields are required. / सभी फ़ील्ड भरना आवश्यक है।"
            elif password != confirm_password:
                error = "Passwords do not match. / पासवर्ड मेल नहीं खाते हैं।"
            elif len(password) < 6:
                error = "Password must be at least 6 characters. / पासवर्ड कम से कम 6 अक्षरों का होना चाहिए।"
            else:
                user.set_password(password)
                user.save()
                logger.info("[PasswordReset] Password updated successfully for user: %s", user.username)
                return redirect("citizen_password_reset_complete")

    return render(request, "citizen_password_reset_confirm.html", {
        "valid": valid,
        "error": error,
        "uidb64": uidb64,
        "token": token
    })


def citizen_password_reset_complete(request):
    """
    GET /citizen/password-reset/complete/ - Displays success page after password update.
    """
    return render(request, "citizen_password_reset_complete.html")

