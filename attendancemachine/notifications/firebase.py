# notifications/firebase.py

import firebase_admin
from firebase_admin import credentials, messaging
from django.conf import settings


def get_firebase_app():
    if firebase_admin._apps:
        return firebase_admin.get_app()

    cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_FILE)
    return firebase_admin.initialize_app(cred)


def send_push_to_tokens(tokens, title, body, data=None):
    get_firebase_app()

    if not tokens:
        return {
            "success": False,
            "message": "No tokens found.",
            "success_count": 0,
            "failure_count": 0,
        }

    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        data=data or {},
        tokens=tokens,
    )

    response = messaging.send_each_for_multicast(message)

    return {
        "success": True,
        "success_count": response.success_count,
        "failure_count": response.failure_count,
        "responses": response.responses,
    }