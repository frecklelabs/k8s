"""Run with Django/allauth installed: python tests/floppy/test_bootstrap.py.

Uses a real SQLite transaction and allauth model, with Django's standard User.
It tests provisioning integrity, not Floppy's complete settings or image startup.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

import django
from django.conf import settings

settings.configure(
    SECRET_KEY="test-only",
    INSTALLED_APPS=[
        "django.contrib.auth", "django.contrib.contenttypes",
        "django.contrib.sites", "allauth", "allauth.account",
        "allauth.socialaccount",
    ],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    MIDDLEWARE=["allauth.account.middleware.AccountMiddleware"],
    SITE_ID=1,
)
django.setup()
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

SCRIPT = Path(__file__).resolve().parents[2] / "kubernetes/apps/media/floppy/bootstrap.py"
NAMES = ["admin-example", "viewer-one", "viewer-two", "viewer-three", "viewer-four", "viewer-five"]
USERS = [
    {"username": name, "subject": f"00000000-0000-4000-8000-{i:012d}",
     "admin": name == "admin-example"}
    for i, name in enumerate(NAMES, 1)
]
User = get_user_model()


def bootstrap(entries=USERS):
    with patch.dict(os.environ, FLOPPY_USERS=json.dumps(entries)):
        with contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(SCRIPT))


class IdentityTests(TestCase):
    def test_new_then_repeat_preserves_user_and_link_ids(self):
        bootstrap()
        before = list(SocialAccount.objects.values_list("pk", "user_id", "uid"))
        bootstrap()
        self.assertEqual(before, list(SocialAccount.objects.values_list("pk", "user_id", "uid")))
        self.assertEqual(User.objects.count(), 6)
        for user in User.objects.all():
            self.assertFalse(user.has_usable_password())
            self.assertEqual(user.is_staff, user.username == "admin-example")
            self.assertEqual(user.is_superuser, user.username == "admin-example")

    def test_unlinked_username_is_never_adopted_or_promoted(self):
        for name in NAMES:
            with self.subTest(username=name):
                user = User.objects.create(username=name, password="preserve-me")
                with self.assertRaisesRegex(ValueError, "exact reviewed OIDC link"):
                    bootstrap()
                user.refresh_from_db()
                self.assertEqual(user.password, "preserve-me")
                self.assertFalse(user.is_superuser)
                self.assertEqual(User.objects.count(), 1)
                self.assertFalse(SocialAccount.objects.exists())
                user.delete()

    def test_subject_assigned_elsewhere_is_rejected(self):
        other = User.objects.create(username="existing-owner")
        SocialAccount.objects.create(user=other, provider="freckleid", uid=USERS[-1]["subject"])
        with self.assertRaisesRegex(ValueError, "different account"):
            bootstrap()
        self.assertEqual(list(User.objects.values_list("username", flat=True)), ["existing-owner"])
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_username_and_subject_on_different_users_is_rejected(self):
        bootstrap()
        account = SocialAccount.objects.get(uid=USERS[0]["subject"])
        account.user = User.objects.get(username="viewer-one")
        account.save()
        with self.assertRaisesRegex(ValueError, "exact reviewed OIDC link"):
            bootstrap()

    def test_unexpected_additional_link_is_rejected(self):
        bootstrap()
        user = User.objects.get(username="viewer-five")
        SocialAccount.objects.create(user=user, provider="other", uid="extra")
        user.password = "preserve-me"
        user.save()
        with self.assertRaisesRegex(ValueError, "additional social identity"):
            bootstrap()
        user.refresh_from_db()
        self.assertEqual(user.password, "preserve-me")
        self.assertEqual(SocialAccount.objects.count(), 7)

    def test_collision_rolls_back_changes_to_earlier_users(self):
        bootstrap()
        first = User.objects.get(username="admin-example")
        first.is_staff = first.is_superuser = False
        first.password = "preserve-me"
        first.save()
        SocialAccount.objects.get(uid=USERS[-1]["subject"]).delete()
        with self.assertRaises(ValueError):
            bootstrap()
        first.refresh_from_db()
        self.assertFalse(first.is_superuser)
        self.assertEqual(first.password, "preserve-me")

    def test_invalid_identity_input_cannot_create_accounts(self):
        for entries in [None, {}, [], [None], [{"username": "incomplete"}],
                        USERS + [USERS[0]],
                        [{**entry, "username": USERS[0]["username"]} for entry in USERS],
                        [{**entry, "username": " "} for entry in USERS],
                        [{**entry, "username": 42} for entry in USERS],
                        [{**entry, "subject": USERS[0]["subject"]} for entry in USERS],
                        [{**entry, "admin": True} for entry in USERS],
                        [{**entry, "admin": False} for entry in USERS],
                        [{**entry, "admin": 1} for entry in USERS],
                        [{**entry, "subject": "invalid"} for entry in USERS],
                        [{**entry, "subject": None} for entry in USERS],
                        [{**entry, "subject": entry["subject"].replace("-", "")} for entry in USERS]]:
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError):
                    bootstrap(entries)
                self.assertFalse(User.objects.exists())
                self.assertFalse(SocialAccount.objects.exists())

    def test_identity_list_and_admin_are_supplied_by_configuration(self):
        entries = [
            {"username": "another-viewer", "subject": USERS[0]["subject"], "admin": False},
            {"username": "another-admin", "subject": USERS[1]["subject"], "admin": True},
        ]
        bootstrap(entries)
        bootstrap(entries)
        self.assertEqual(User.objects.count(), 2)
        self.assertFalse(User.objects.get(username="another-viewer").is_superuser)
        self.assertTrue(User.objects.get(username="another-admin").is_superuser)


if __name__ == "__main__":
    call_command("migrate", verbosity=0)
    unittest.main()
