"""Provision only the reviewed OIDC subjects, with local passwords disabled."""
import json
import os
import uuid

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.db import transaction

users = json.loads(os.environ["FLOPPY_USERS"])
if not isinstance(users, list) or not users:
    raise ValueError("Expected a non-empty list of OIDC identities")
for entry in users:
    if not isinstance(entry, dict) or set(entry) != {"username", "subject", "admin"}:
        raise ValueError("Each identity needs username, subject and admin fields")
    username = entry["username"]
    if not isinstance(username, str) or not username.strip() or username != username.strip():
        raise ValueError("Usernames must be non-empty strings without outer whitespace")
    subject = entry["subject"]
    if not isinstance(subject, str) or str(uuid.UUID(subject)) != subject:
        raise ValueError("OIDC subjects must be canonical UUID strings")
    if type(entry["admin"]) is not bool:
        raise ValueError("Admin flags must be booleans")
if len({entry["username"] for entry in users}) != len(users):
    raise ValueError("Usernames must be distinct")
if len({entry["subject"] for entry in users}) != len(users):
    raise ValueError("OIDC subjects must be distinct")
if sum(entry["admin"] for entry in users) != 1:
    raise ValueError("Exactly one identity must be admin")

with transaction.atomic():
    for entry in users:
        user = get_user_model().objects.filter(username=entry["username"]).first()
        account = SocialAccount.objects.filter(
            provider="freckleid", uid=entry["subject"]
        ).first()
        # An existing username alone is never proof of identity or consent to link.
        if user is not None:
            if account is None or account.user_id != user.pk:
                raise ValueError("Existing username lacks its exact reviewed OIDC link")
        else:
            if account is not None:
                raise ValueError("Existing OIDC link belongs to a different account")
            user = get_user_model().objects.create(username=entry["username"])
            account = SocialAccount.objects.create(
                provider="freckleid", uid=entry["subject"], user=user
            )
        if SocialAccount.objects.filter(user=user).exclude(pk=account.pk).exists():
            raise ValueError("Unexpected additional social identity; review before startup")
        user.set_unusable_password()
        user.is_staff = entry["admin"]
        user.is_superuser = entry["admin"]
        user.save(update_fields=["password", "is_staff", "is_superuser"])
print("Verified OIDC-only accounts; admin rights restricted to the configured admin identity")
