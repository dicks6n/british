# views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from .models import FlightClaim 
from django.contrib.auth.decorators import login_required
from django.contrib import messages


def home(request):
    if request.method == 'POST':
        claim_type = request.POST.get('claim_type', 'delayed')
        
        claim = FlightClaim(
            claim_type=claim_type,
            user=request.user if request.user.is_authenticated else None,
            status='pending'
        )

        if claim_type == 'delayed':
            claim.from_airport = request.POST.get('from_airport')
            claim.to_airport = request.POST.get('to_airport')
            claim.flight_date = request.POST.get('flight_date') or None
            claim.booking_no = request.POST.get('booking_no')
            claim.delay_duration = request.POST.get('delay_duration')
            
        elif claim_type == 'cancelled':
            claim.from_airport = request.POST.get('from_airport_cancelled')
            claim.to_airport = request.POST.get('to_airport_cancelled')
            claim.flight_date = request.POST.get('flight_date_cancelled') or None
            claim.booking_no = request.POST.get('airline_cancelled')
            claim.cancellation_notice = request.POST.get('cancellation_notice')
            
        elif claim_type == 'connection':
            claim.from_airport = request.POST.get('conn_from')
            claim.to_airport = request.POST.get('conn_to')
            claim.flight_date = request.POST.get('conn_date') or None
            claim.booking_no = request.POST.get('conn_airline')
            
        elif claim_type == 'denied':
            claim.from_airport = request.POST.get('denied_from')
            claim.to_airport = request.POST.get('denied_to')
            claim.flight_date = request.POST.get('denied_date') or None
            claim.booking_no = request.POST.get('denied_airline')
            
        elif claim_type == 'baggage':
            claim.from_airport = request.POST.get('baggage_airport')
            claim.flight_date = request.POST.get('baggage_date') or None
            claim.booking_no = request.POST.get('baggage_airline')
            claim.pir_number = request.POST.get('pir_number')
            claim.baggage_issue_type = request.POST.get('baggage_issue_type')

        claim.save()
        return redirect('refund', claim_id=claim.id)

    return render(request, 'store/home.html')

def refund_view(request, claim_id):
    claim = get_object_or_404(FlightClaim, id=claim_id)

    if request.method == "POST":
        # Update the claim with personal contact details when submitting the application[cite: 5]
        claim.fullname = request.POST.get("fullname", claim.fullname)
        claim.email = request.POST.get("email", claim.email)
        claim.phone = request.POST.get("phone", claim.phone)
        claim.save()
        
        # Render the page back with a success/submitted state[cite: 5]
        return render(request, "store/refund.html", {
            "claim": claim,
            "status": claim.status,
            "approved_amount": claim.approved_amount,
            "submitted": True
        })

    return render(request, "store/refund.html", {
        "claim": claim,
        "status": claim.status,
        "approved_amount": claim.approved_amount,
    })


# ==========================================
# AUTHENTICATION VIEWS
# ==========================================
def login_view(request):
    if request.method == "POST":
        login_input = request.POST.get("login")  # Matches name="login" in template
        password = request.POST.get("password")
        
        user = None
        if login_input:
            if "@" in login_input:
                try:
                    matched_user = User.objects.get(email=login_input)
                    user = authenticate(request, username=matched_user.username, password=password)
                except User.DoesNotExist:
                    user = None
            else:
                user = authenticate(request, username=login_input, password=password)
        
        if user is not None:
            login(request, user)
            return redirect("claims")  # Update with your target dashboard URL name
        else:
            messages.error(request, "Invalid credentials. Please check your username/email and password.")
            
    return render(request, "store/login.html")

def signup_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect('signup')

        if User.objects.filter(email=email).exists():
            messages.error(request, "Email address is already registered.")
            return redirect('signup')

        User.objects.create_user(username=username, email=email, password=password)
        messages.success(request, "Registration successful! You can now log in.")
        return redirect('login')

    return render(request, 'store/signup.html')


def logout_view(request):
    logout(request)
    return redirect("login")


def air_passenger_rights_view(request):
    return render(request, 'store/rights.html')


from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

def send_claim_update_email(claim):
    subject = f"Update regarding your flight compensation claim #{claim.id}"
    html_message = render_to_string('store/emails/claim_update.html', {'claim': claim})
    plain_message = strip_tags(html_message)
    recipient_list = [claim.email]

    send_mail(
        subject,
        plain_message,
        'support@yourdomain.com',
        recipient_list,
        html_message=html_message,
        fail_silently=False,
    )

def how_it_works_view(request):
    """
    Renders the 'How it works' information page.
    """
    return render(request, 'store/about.html')



@login_required(login_url='login')
def claim_views(request):
    """
    Displays approved and cancelled claims strictly for the logged-in user.
    """
    approved_claims = FlightClaim.objects.filter(user=request.user, status='approved')
    cancelled_claims = FlightClaim.objects.filter(user=request.user, status='cancelled')
    
    context = {
        'approved_claims': approved_claims,
        'cancelled_claims': cancelled_claims,
    }

    return render(request, "store/claims.html", context)