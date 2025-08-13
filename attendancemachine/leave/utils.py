import os
import ssl
import certifi
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from openai import OpenAI

# ✅ SSL fix for cPanel
os.environ['SSL_CERT_FILE'] = certifi.where()
ssl._create_default_https_context = ssl.create_default_context

# --- OpenRouter Setup ---
client = OpenAI(
    api_key=settings.OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

def correct_grammar_and_paraphrase(text: str) -> str:
    try:
        print("🔍 Correcting grammar via OpenRouter...")
        completion = client.chat.completions.create(
            model="meta-llama/llama-3-70b-instruct",
            messages=[
                {
                    "role": "user",
                    "content": f"Correct this sentence without explanation. Only return the corrected sentence:\n{text}"
                }
            ],
            extra_headers={
                "HTTP-Referer": "http://localhost:8000",  # Optional
                "X-Title": "AttendanceMachine"             # Optional
            }
        )
        corrected = completion.choices[0].message.content.strip()
        print("✅ Corrected sentence:", corrected)
        return corrected
    except Exception as e:
        print("❌ Grammar correction failed:", str(e))
        return text  # fallback


def send_leave_email(user, leave, corrected_reason, approve_url=None, reject_url=None):
    """
    Sends an email notification for a leave request to the manager.
    """
    print("📨 Preparing email for:", user.email)
    print("📨 Using corrected_reason:", corrected_reason)

    subject = f"New Leave Request from {user.username}"

    context = {
        'user': user,
        'leave': leave,
        'corrected_reason': corrected_reason,
        'approve_url': approve_url,
        'reject_url': reject_url,
    }

    body = render_to_string("leave/leave_email.html", context)
    print("🧾 Rendered email body (truncated):", body[:200])

    email = EmailMessage(subject, body, to=["jamil@ampec.com.au"])
    email.content_subtype = "html"

    try:
        email.send()
        print("✅ Email sent successfully.")
    except Exception as e:
        print("❌ Email send error:", str(e))

    return body
