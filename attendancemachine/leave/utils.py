from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings

from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import torch

# Load grammar correction model
grammar_tokenizer = AutoTokenizer.from_pretrained("vennify/t5-base-grammar-correction")
grammar_model = AutoModelForSeq2SeqLM.from_pretrained("vennify/t5-base-grammar-correction")

# Load paraphrasing model
paraphrase_tokenizer = AutoTokenizer.from_pretrained("Vamsi/T5_Paraphrase_Paws")
paraphrase_model = AutoModelForSeq2SeqLM.from_pretrained("Vamsi/T5_Paraphrase_Paws")


def correct_grammar_and_paraphrase(text):
    try:
        # Step 1: Grammar correction (3 passes)
        for _ in range(3):
            input_text = f"grammar: {text}"
            inputs = grammar_tokenizer.encode(input_text, return_tensors="pt", truncation=True)
            outputs = grammar_model.generate(inputs, max_length=128, num_beams=5, early_stopping=True)
            text = grammar_tokenizer.decode(outputs[0], skip_special_tokens=True)

        print("✅ Grammar corrected:", text)

        # Step 2: Paraphrasing
        paraphrase_input = f"paraphrase: {text} </s>"
        para_inputs = paraphrase_tokenizer.encode(paraphrase_input, return_tensors="pt", truncation=True)
        para_outputs = paraphrase_model.generate(para_inputs, max_length=128, num_beams=5, num_return_sequences=1, early_stopping=True)
        final_text = paraphrase_tokenizer.decode(para_outputs[0], skip_special_tokens=True)

        print("🔁 Paraphrased:", final_text)
        return final_text

    except Exception as e:
        print("⚠️ Correction/Paraphrasing error:", str(e))
        return text  # fallback


def send_leave_email(user, leave, corrected_reason, approve_url=None, reject_url=None):
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
    email.send()

    return body
