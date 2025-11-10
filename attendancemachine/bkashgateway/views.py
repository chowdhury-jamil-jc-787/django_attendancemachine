import uuid
from django.http import JsonResponse, HttpResponseRedirect, HttpResponse
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from mealreport.models import MealPayment
from meal.models import CookRecord

User = get_user_model()


# ----------------------------------------
# 1️⃣  bKash Pay (Start Demo Payment)
# ----------------------------------------
@api_view(["GET"])
@permission_classes([])  # Open for demo (no login required)
def bkash_pay(request):
    """
    Start a fake sandbox bKash payment.
    Frontend calls:
        GET /api/bkash/pay/?date=YYYY-MM-DD

    - Creates or updates pending MealPayment record
    - Redirects user to bKash sandbox demo portal
    """

    date_str = request.GET.get("date")
    d = parse_date(date_str)
    if not d:
        return JsonResponse({"error": "date parameter is required"}, status=400)

    rec = CookRecord.objects.filter(date=d).first()
    if not rec:
        return JsonResponse({"error": "CookRecord not found"}, status=404)

    # Create invoice ID
    invoice = f"DEMO-{uuid.uuid4().hex[:8]}"

    # Determine payer
    paid_user = request.user if request.user.is_authenticated else User.objects.filter(
        username__iexact="frahman"
    ).first()

    if not paid_user:
        return JsonResponse({"error": "Fallback user (frahman) not found."}, status=400)

    # Save or update pending payment
    MealPayment.objects.update_or_create(
        date=d,
        defaults={
            "amount": rec.price * rec.eaters_count,
            "currency": "BDT",
            "method": "bkash",
            "status": "pending",
            "transaction_id": f"pending_{invoice}",
            "invoice_number": invoice,
            "remarks": "Waiting for sandbox confirmation",
            "paid_by": paid_user,
        },
    )

    # ✅ Redirect user to working sandbox demo page
    demo_url = (
        "https://merchantdemo.sandbox.bka.sh/"
        f"?invoice={invoice}"
        f"&redirect_url=http://127.0.0.1:8000/api/bkash/callback/?date={d}"
    )

    return HttpResponseRedirect(demo_url)


# ----------------------------------------
# 2️⃣  bKash Callback (After Payment)
# ----------------------------------------
@api_view(["GET"])
@permission_classes([])  # Open for demo
def bkash_callback(request):
    """
    Called when user finishes demo payment.
    Marks MealPayment as successful and shows confirmation.
    """

    date_str = request.GET.get("date")
    d = parse_date(date_str)
    if not d:
        return JsonResponse({"error": "Invalid or missing date"}, status=400)

    rec = CookRecord.objects.filter(date=d).first()
    if not rec:
        return JsonResponse({"error": "CookRecord not found"}, status=404)

    pay = MealPayment.objects.filter(date=d).first()
    if not pay:
        return JsonResponse({"error": "Payment record not found"}, status=404)

    # ✅ Mark payment as success
    pay.status = "success"
    pay.transaction_id = f"demo_trx_{uuid.uuid4().hex[:10]}"
    pay.remarks = "Paid via bKash sandbox demo"
    pay.paid_at = timezone.now()
    pay.save(update_fields=["status", "transaction_id", "remarks", "paid_at"])

    # ✅ Return confirmation HTML page
    html = f"""
    <html>
        <head><title>bKash Demo Payment Success</title></head>
        <body style="font-family:sans-serif; text-align:center; margin-top:100px;">
            <h2>✅ bKash Demo Payment Successful!</h2>
            <p><strong>Date:</strong> {d}</p>
            <p><strong>Amount:</strong> {pay.amount} BDT</p>
            <p><strong>Transaction ID:</strong> {pay.transaction_id}</p>
            <p><strong>Invoice:</strong> {pay.invoice_number}</p>
            <p>Status: <b style="color:green;">Success</b></p>
            <hr style="margin:20px 0;">
            <p><a href="http://127.0.0.1:5173" style="text-decoration:none; color:#007bff;">← Back to Dashboard</a></p>
        </body>
    </html>
    """
    return HttpResponse(html)


# ----------------------------------------
# 3️⃣  bKash Status (Optional API)
# ----------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bkash_status(request):
    """
    Helper to check current payment status
    GET /api/bkash/status/?date=YYYY-MM-DD
    """

    date_str = request.GET.get("date")
    d = parse_date(date_str)
    if not d:
        return JsonResponse({"error": "Invalid date"}, status=400)

    pay = MealPayment.objects.filter(date=d).first()
    if not pay:
        return JsonResponse({"message": "No payment found for this date."}, status=200)

    return JsonResponse(
        {
            "date": str(d),
            "amount": pay.amount,
            "status": pay.status,
            "method": pay.method,
            "transaction_id": pay.transaction_id,
            "paid_at": pay.paid_at,
            "remarks": pay.remarks,
        },
        status=200,
    )
