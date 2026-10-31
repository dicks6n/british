import json
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import (
    FlightClaim, SupportCall, CallSignal, ClaimEvidence,
    ChatConversation, ChatMessage,
)
from .eligibility import evaluate

OTHER_SIDE = {'customer': 'agent', 'agent': 'customer'}


# ============================================================
# HELPERS
# ============================================================
def _expire_ringing():
    now = timezone.now()
    SupportCall.objects.filter(
        status='ringing',
        started_at__lt=now - timedelta(seconds=getattr(settings, 'CALL_RING_TIMEOUT', 45)),
    ).update(status='missed', ended_at=now)
    # Heartbeats: both browsers poll call_detail about once a second during a
    # call. A connected call one side has gone silent on (browser closed,
    # phone died) is ended, so it can't block that agent or claim.
    stale = now - timedelta(seconds=getattr(settings, 'CALL_HEARTBEAT_TIMEOUT', 20))
    SupportCall.objects.filter(status='connected', accepted_at__lt=stale).filter(
        Q(customer_seen_at__isnull=True) | Q(customer_seen_at__lt=stale)
        | Q(agent_seen_at__isnull=True) | Q(agent_seen_at__lt=stale),
    ).update(status='ended', ended_at=now)


def _fix_sdp(sdp):
    """Normalise SDP text. It must use CRLF line endings and END with CRLF.

    Never .strip() SDP: removing the final CRLF makes browsers reject the call
    with "Failed to parse SessionDescription ... Invalid SDP line".
    """
    if isinstance(sdp, dict):
        sdp = sdp.get('sdp', '')
    sdp = str(sdp or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    return sdp.replace('\n', '\r\n') + '\r\n' if sdp else ''


def _resolve_side(request, side, data=None):
    """Return the claim the caller may act for, or None for agents."""
    if side == 'customer':
        claim = None
        # 1. The claim shown on the support page (saved in the session by support_view).
        cid = request.session.get('support_claim_id')
        # 2. An explicit claim ID (?claim= or claim_id in the JSON body).
        cid = cid or request.GET.get('claim') or (data or {}).get('claim_id')
        if cid:
            claim = FlightClaim.objects.filter(claim_id=str(cid).upper()).first()
        # 3. A logged-in customer's open claim.
        if not claim and request.user.is_authenticated:
            claim = FlightClaim.objects.filter(
                user=request.user, status__in=('pending', 'eligible', 'approved')
            ).first()
        if not claim:
            raise PermissionError('You need a claim ID before you can call. Apply for a claim first.')
        return claim
    if side == 'agent':
        if not (request.user.is_authenticated and request.user.is_staff):
            raise PermissionError('Staff login required.')
        return None
    raise PermissionError('Invalid side.')


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _get_call_for(request, call_id, side, data=None):
    claim = _resolve_side(request, side, data)
    qs = SupportCall.objects.select_related('claim', 'agent')
    if claim is not None:
        qs = qs.filter(claim=claim)
    return qs.filter(pk=call_id).first()


# ============================================================
# HOME / CLAIM INTAKE
# ============================================================
def home(request):
    if request.method == 'POST':
        claim = FlightClaim(
            claim_type=request.POST.get('claim_type', 'delayed'),
            user=request.user if request.user.is_authenticated else None,
        )
        claim.airline_iata    = (request.POST.get('airline_iata') or '')[:3]
        claim.airline_name    = (request.POST.get('airline_name') or '')[:255]
        claim.airline_country = (request.POST.get('airline_country') or '')[:2]

        claim.from_iata    = (request.POST.get('from_iata') or '')[:4]
        claim.from_name    = (request.POST.get('from_name') or '')[:255]
        claim.from_city    = (request.POST.get('from_city') or '')[:120]
        claim.from_country = (request.POST.get('from_country') or '')[:2]
        try: claim.from_lat = float(request.POST.get('from_lat') or 'nan')
        except (TypeError, ValueError): claim.from_lat = None
        try: claim.from_lng = float(request.POST.get('from_lng') or 'nan')
        except (TypeError, ValueError): claim.from_lng = None

        claim.to_iata    = (request.POST.get('to_iata') or '')[:4]
        claim.to_name    = (request.POST.get('to_name') or '')[:255]
        claim.to_city    = (request.POST.get('to_city') or '')[:120]
        claim.to_country = (request.POST.get('to_country') or '')[:2]
        try: claim.to_lat = float(request.POST.get('to_lat') or 'nan')
        except (TypeError, ValueError): claim.to_lat = None
        try: claim.to_lng = float(request.POST.get('to_lng') or 'nan')
        except (TypeError, ValueError): claim.to_lng = None

        claim.booking_no = request.POST.get('booking_no') or None
        fd = request.POST.get('flight_date')
        claim.flight_date = fd if fd else None

        claim.delay_duration      = request.POST.get('delay_duration')
        claim.cancellation_notice = request.POST.get('cancellation_notice')
        claim.alternative_offered = request.POST.get('alternative_offered')
        claim.conn_from           = request.POST.get('conn_from')
        claim.conn_middle         = request.POST.get('conn_middle')
        claim.conn_to             = request.POST.get('conn_to')
        claim.connection_time     = request.POST.get('connection_time')
        claim.denied_alternative  = request.POST.get('denied_alternative')
        claim.baggage_airport     = request.POST.get('baggage_airport')
        claim.pir_number          = request.POST.get('pir_number')
        claim.baggage_issue_type  = request.POST.get('baggage_issue_type')

        # Pre-fill contact details from the signed-in account so the customer's
        # real name shows on their chat and calls (SupportCall.customer_name
        # comes from claim.fullname).
        if request.user.is_authenticated:
            claim.fullname = claim.fullname or (request.user.get_full_name() or None)
            claim.email    = claim.email or (request.user.email or None)
            claim.phone    = claim.phone or request.session.get('signup_phone') or None

        claim.save()

        # Run eligibility engine
        result = evaluate(claim)
        for k, v in result.to_claim_fields().items():
            setattr(claim, k, v)
        claim.status = 'pending'
        claim.save()

        return redirect('claim_result', pk=claim.pk)

    return render(request, 'store/home.html')


def claim_result(request, pk):
    claim = get_object_or_404(FlightClaim, pk=pk)
    if request.method == 'POST':
        claim.fullname = request.POST.get('fullname', claim.fullname)
        claim.email    = request.POST.get('email', claim.email)
        claim.phone    = request.POST.get('phone', claim.phone)
        claim.save()
        for f in request.FILES.getlist('evidence'):
            ClaimEvidence.objects.create(claim=claim, kind='other', file=f)
        return render(request, 'store/refund.html', {'claim': claim, 'submitted': True})
    return render(request, 'store/refund.html', {'claim': claim})


def refund_view(request, claim_id):
    claim = get_object_or_404(FlightClaim, id=claim_id)
    if request.method == "POST":
        claim.fullname = request.POST.get("fullname", claim.fullname)
        claim.email    = request.POST.get("email", claim.email)
        claim.phone    = request.POST.get("phone", claim.phone)
        claim.save()
        return render(request, "store/refund.html", {"claim": claim, "submitted": True})
    return render(request, "store/refund.html", {"claim": claim})


def air_passenger_rights_view(request):
    return render(request, 'store/rights.html')


def how_it_works_view(request):
    return render(request, 'store/about.html')


# ============================================================
# AUTH
# ============================================================
def login_view(request):
    if request.method == "POST":
        inp = request.POST.get("login")
        pwd = request.POST.get("password")
        user = None
        if inp:
            if "@" in inp:
                try:
                    u = User.objects.get(email=inp)
                    user = authenticate(request, username=u.username, password=pwd)
                except User.DoesNotExist:
                    pass
            else:
                user = authenticate(request, username=inp, password=pwd)
        if user:
            login(request, user)
            return redirect("claims")
        messages.error(request, "Invalid credentials.")
    return render(request, "store/login.html")


def signup_view(request):
    if request.method == 'POST':
        fullname = (request.POST.get('fullname') or '').strip()
        email    = (request.POST.get('email') or '').strip().lower()
        phone    = (request.POST.get('phone') or '').strip()
        password = request.POST.get('password') or ''
        confirm  = request.POST.get('confirm') or ''

        # Keep what they typed so the form can be re-filled on error
        form = {'fullname': fullname, 'email': email, 'phone': phone}

        if not fullname or not email or not password:
            messages.error(request, "Please fill in your name, email and a password.")
            return render(request, 'store/signup.html', {'form': form})
        if password != confirm:
            messages.error(request, "The two passwords do not match.")
            return render(request, 'store/signup.html', {'form': form})
        if len(password) < 6:
            messages.error(request, "Password must be at least 6 characters.")
            return render(request, 'store/signup.html', {'form': form})
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username=email).exists():
            messages.error(request, "That email is already registered. Please log in.")
            return render(request, 'store/signup.html', {'form': form})

        # Log in with the email; split the full name into first / last so it
        # shows up everywhere (get_full_name powers the chat and call name).
        parts = fullname.split()
        user = User.objects.create_user(
            username=email, email=email, password=password,
            first_name=parts[0], last_name=' '.join(parts[1:]),
        )
        login(request, user)
        if phone:
            request.session['signup_phone'] = phone   # used to prefill the claim form
        messages.success(request, "Welcome, " + user.first_name + "! Your account is ready.")
        return redirect('home')
    return render(request, 'store/signup.html')


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required(login_url='login')
def claim_views(request):
    approved  = FlightClaim.objects.filter(user=request.user, status='approved')
    cancelled = FlightClaim.objects.filter(user=request.user, status='cancelled')
    pending   = FlightClaim.objects.filter(user=request.user, status__in=('pending', 'eligible'))
    return render(request, "store/claims.html", {
        'approved_claims': approved,
        'cancelled_claims': cancelled,
        'pending_claims': pending,
    })


# ============================================================
# AIRPORT AUTOCOMPLETE
# ============================================================
@require_POST
def airport_lookup(request):
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "bad json"}, status=400)
    q = (body.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"ok": True, "results": []})

    import requests as http_requests
    try:
        r = http_requests.get(
            f"https://aerodatabox.p.rapidapi.com/airports/search/term",
            params={"q": q, "limit": 8},
            headers={
                "X-RapidAPI-Key": getattr(settings, 'AERODATABOX_API_KEY', ''),
                "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com",
            },
            timeout=6,
        )
        data = r.json()
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

    results = []
    for it in (data.get("items") or []):
        loc = it.get("location") or {}
        results.append({
            "iata": it.get("iata") or "",
            "name": it.get("name") or "",
            "city": it.get("municipalityName") or "",
            "country": (it.get("countryCode") or "").upper()[:2],
            "lat": loc.get("lat"),
            "lng": loc.get("lon"),
        })
    return JsonResponse({"ok": True, "results": results})


# ============================================================
# SUPPORT (customer page)
# ============================================================
@ensure_csrf_cookie
def support_view(request):
    claim_id = request.GET.get('claim') or request.session.get('support_claim_id')
    claim = None
    if claim_id:
        claim = FlightClaim.objects.filter(claim_id=str(claim_id).upper()).first()
    if not claim and request.user.is_authenticated:
        claim = FlightClaim.objects.filter(user=request.user).order_by('-created_at').first()
    # The call API uses this, so it always acts for the claim shown on the page.
    if claim:
        request.session['support_claim_id'] = claim.claim_id
    else:
        request.session.pop('support_claim_id', None)

    return render(request, 'store/support.html', {
        'claim': claim,
        'ice_servers': getattr(settings, 'WEBRTC_ICE_SERVERS', [
            {'urls': 'stun:stun.l.google.com:19302'},
        ]),
    })


# ============================================================
# STAFF CONSOLE
# ============================================================
@staff_member_required
@ensure_csrf_cookie
def staff_console(request):
    _expire_ringing()
    return render(request, 'store/staff_console.html', {
        'claims': FlightClaim.objects.all()[:200],
        'recent_calls': SupportCall.objects.filter(claim__isnull=False)
                                            .select_related('claim', 'agent')[:20],
        'ice_servers': getattr(settings, 'WEBRTC_ICE_SERVERS', [
            {'urls': 'stun:stun.l.google.com:19302'},
        ]),
    })

# ============================================================
# CALL API — mirrors the calls app exactly
# ============================================================
@require_POST
def start_call(request):
    data = _body(request)
    side = data.get('side')
    offer = _fix_sdp(data.get('offer'))
    if not offer.startswith('v='):
        return _error('Missing or invalid call offer.')

    try:
        claim = _resolve_side(request, side, data)
    except PermissionError as exc:
        return _error(str(exc), 403)

    if side == 'agent':
        claim_id = str(data.get('claim_id', '')).upper()
        claim = FlightClaim.objects.filter(claim_id=claim_id).first()
        if claim is None:
            return _error('Claim not found.', 404)

    # ─────────── AGGRESSIVE CLEANUP BEFORE CHECKING BUSY ───────────
    now = timezone.now()

    # A) Any "ringing" call for this claim older than 20 seconds is dead
    #    (20s is short enough that retries work quickly)
    SupportCall.objects.filter(
        claim=claim,
        status='ringing',
        started_at__lt=now - timedelta(seconds=20),
    ).update(status='missed', ended_at=now)

    # B) Any "connected" call for this claim with no accepted_at is broken
    SupportCall.objects.filter(
        claim=claim,
        status='connected',
        accepted_at__isnull=True,
    ).update(status='ended', ended_at=now)

    # C) Any "connected" call older than 2 hours is dead
    SupportCall.objects.filter(
        claim=claim,
        status='connected',
        accepted_at__lt=now - timedelta(hours=2),
    ).update(status='ended', ended_at=now)

    # D) For agents: same cleanup on their own calls
    if side == 'agent':
        SupportCall.objects.filter(
            agent=request.user,
            status='ringing',
            started_at__lt=now - timedelta(seconds=20),
        ).update(status='missed', ended_at=now)
        SupportCall.objects.filter(
            agent=request.user,
            status='connected',
            accepted_at__isnull=True,
        ).update(status='ended', ended_at=now)
        SupportCall.objects.filter(
            agent=request.user,
            status='connected',
            accepted_at__lt=now - timedelta(hours=2),
        ).update(status='ended', ended_at=now)
    # ─────────────────────────────────────────────────────────────

    with transaction.atomic():
        busy = SupportCall.objects.select_for_update().filter(
            claim=claim, status__in=SupportCall.OPEN_STATUSES
        )
        if side == 'agent':
            if SupportCall.objects.filter(
                agent=request.user, status__in=SupportCall.OPEN_STATUSES
            ).exists():
                return _error('You are already on a call.', 409)
        if busy.exists():
            return _error('This line is busy. Please try again shortly.', 409)

        call = SupportCall.objects.create(
            claim=claim,
            customer_name=(claim.fullname if claim and claim.fullname else 'Guest'),
            initiated_by=side,
            offer=offer,
            agent=request.user if side == 'agent' else None,
        )
    return JsonResponse(call.to_dict(), status=201)



@require_GET
def incoming_calls(request):
    side = request.GET.get('side')
    try:
        claim = _resolve_side(request, side)
    except PermissionError as exc:
        return _error(str(exc), 403)

    _expire_ringing()
    qs = SupportCall.objects.select_related('claim', 'agent').filter(
        status='ringing', initiated_by=OTHER_SIDE[side]
    )
    if claim is not None:
        qs = qs.filter(claim=claim)
    return JsonResponse({'calls': [c.to_dict() for c in qs.order_by('started_at')[:10]]})

@require_GET
def call_detail(request, call_id):
    side = request.GET.get('side')
    try:
        call = _get_call_for(request, call_id, side)
    except PermissionError as exc:
        return _error(str(exc), 403)
    if call is None:
        return _error('Call not found.', 404)
    SupportCall.objects.filter(pk=call.pk).update(**{f'{side}_seen_at': timezone.now()})
    _expire_ringing()
    call.refresh_from_db()
    return JsonResponse(call.to_dict(include_sdp=True))


@require_POST
def answer_call(request, call_id):
    data = _body(request)
    side = data.get('side')
    answer_sdp = _fix_sdp(data.get('answer'))

    # ── HARD VALIDATION — the SDP must start with "v=" ──
    if not answer_sdp.startswith('v='):
        return JsonResponse({
            'error': 'Invalid answer SDP. Got: ' + repr(answer_sdp[:60]),
        }, status=400)

    try:
        call = _get_call_for(request, call_id, side, data)
    except PermissionError as exc:
        return _error(str(exc), 403)
    if call is None:
        return _error('Call not found.', 404)
    if call.initiated_by == side:
        return _error('You cannot answer your own call.')

    _expire_ringing()
    with transaction.atomic():
        call = SupportCall.objects.select_for_update().get(pk=call.pk)
        if call.status != 'ringing':
            return _error('This call is no longer ringing.', 409)
        if side == 'agent':
            if SupportCall.objects.filter(
                agent=request.user, status='connected'
            ).exists():
                return _error('You are already on a call.', 409)
            call.agent = request.user
        call.answer = answer_sdp
        call.status = 'connected'
        call.accepted_at = timezone.now()
        call.save()
    return JsonResponse(call.to_dict(include_sdp=True))   # ← include_sdp=True!


@require_POST
def end_call(request, call_id):
    data = _body(request)
    side = data.get('side')
    try:
        call = _get_call_for(request, call_id, side, data)
    except PermissionError as exc:
        return _error(str(exc), 403)
    if call is None:
        return _error('Call not found.', 404)

    with transaction.atomic():
        call = SupportCall.objects.select_for_update().get(pk=call.pk)
        if call.status in SupportCall.OPEN_STATUSES:
            if call.status == 'connected':
                call.status = 'ended'
            elif call.initiated_by == side:
                call.status = 'missed'
            else:
                call.status = 'declined'
            call.ended_by = side
            call.ended_at = timezone.now()
            call.save()
    return JsonResponse(call.to_dict())


# ============================================================
# STAFF CLAIM HUB
# ============================================================
@staff_member_required
def staff_hub(request):
    claims = FlightClaim.objects.all().order_by('-created_at')
    return render(request, 'store/staff_hub.html', {
        'claims': claims,
        'total_claims': claims.count(),
        'pending_claims': claims.filter(status='pending').count(),
        'approved_claims': claims.filter(status='approved').count(),
        'total_amount': sum((c.eligible_amount or 0) for c in claims.filter(status='approved')),
    })


@staff_member_required
@require_GET
def claim_detail_json(request, claim_id):
    claim = get_object_or_404(FlightClaim, pk=claim_id)
    return JsonResponse({
        'ok': True,
        'claim': {
            'id': claim.pk,
            'claim_id': claim.claim_id,
            'claim_type': claim.get_claim_type_display(),
            'status': claim.get_status_display(),
            'status_raw': claim.status,
            'fullname': claim.fullname or 'Guest',
            'email': claim.email or '—',
            'phone': claim.phone or '—',
            'airline': f"{claim.airline_iata} {claim.airline_name}" if claim.airline_iata else '—',
            'airline_country': claim.airline_country or '—',
            'from_iata': claim.from_iata or '—',
            'from_name': claim.from_name or '—',
            'from_city': claim.from_city or '—',
            'from_country': claim.from_country or '—',
            'to_iata': claim.to_iata or '—',
            'to_name': claim.to_name or '—',
            'to_city': claim.to_city or '—',
            'to_country': claim.to_country or '—',
            'route_distance': round(claim.route_distance_km() or 0),
            'booking_no': claim.booking_no or '—',
            'flight_date': claim.flight_date.strftime('%b %d, %Y') if claim.flight_date else '—',
            'delay_duration': claim.delay_duration or '—',
            'cancellation_notice': claim.cancellation_notice or '—',
            'alternative_offered': claim.alternative_offered or '—',
            'conn_from': claim.conn_from or '—',
            'conn_middle': claim.conn_middle or '—',
            'conn_to': claim.conn_to or '—',
            'connection_time': claim.connection_time or '—',
            'denied_alternative': claim.denied_alternative or '—',
            'baggage_airport': claim.baggage_airport or '—',
            'pir_number': claim.pir_number or '—',
            'baggage_issue_type': claim.baggage_issue_type or '—',
            'jurisdiction': claim.get_jurisdiction_display(),
            'eligible_amount': str(claim.eligible_amount) if claim.eligible_amount else None,
            'eligibility_notes': claim.eligibility_notes or '—',
            'admin_decision_notes': claim.admin_decision_notes or '—',
            'created_at': claim.created_at.strftime('%b %d, %Y · %H:%M'),
            'evidence': [
                {'kind': e.get_kind_display(), 'url': e.file.url, 'note': e.note}
                for e in claim.evidence.all()
            ],
        },
    })


@staff_member_required
@require_POST
def claim_approve(request, claim_id):
    claim = get_object_or_404(FlightClaim, pk=claim_id)
    amount = request.POST.get('amount')
    status = request.POST.get('status', 'approved')

    if amount:
        try:
            claim.eligible_amount = float(amount)
        except ValueError:
            return JsonResponse({'ok': False, 'error': 'Invalid amount format.'}, status=400)

    claim.status = status
    claim.decided_at = timezone.now()
    claim.save()
    return JsonResponse({'ok': True})


# ============================================================
# CHAT
# ============================================================
def _get_or_create_customer_conversation(request):
    if request.user.is_authenticated:
        conv = ChatConversation.objects.filter(
            user=request.user, closed=False
        ).order_by('-last_message_at').first()
        if conv: return conv
        return ChatConversation.objects.create(user=request.user)
    conv = ChatConversation.objects.filter(
        guest_email__isnull=True, guest_name__isnull=True, closed=False
    ).first()
    if conv: return conv
    return ChatConversation.objects.create(guest_name='Guest')


def _ice_servers():
    return getattr(settings, 'WEBRTC_ICE_SERVERS', [{'urls': 'stun:stun.l.google.com:19302'}])


def _claim_for_conversation(conversation):
    """Best-effort: find the claim behind a chat conversation so the call can
    key off it. Matches the logged-in user, else the guest email."""
    claim = None
    if conversation.user_id:
        claim = FlightClaim.objects.filter(user=conversation.user).order_by('-created_at').first()
    if not claim and conversation.guest_email:
        claim = FlightClaim.objects.filter(email__iexact=conversation.guest_email).order_by('-created_at').first()
    return claim


def chat_customer_view(request):
    conversation = _get_or_create_customer_conversation(request)
    conversation.customer_unread = 0
    conversation.save()
    # The call reuses the customer's claim (same rule as the support page).
    claim = None
    if request.user.is_authenticated:
        claim = FlightClaim.objects.filter(
            user=request.user, status__in=('pending', 'eligible', 'approved')
        ).order_by('-created_at').first()
    if not claim:
        claim = _claim_for_conversation(conversation)
    if claim:
        request.session['support_claim_id'] = claim.claim_id
    return render(request, 'store/chat_customer.html', {
        'conversation': conversation,
        'ice_servers': _ice_servers(),
        'call_claim_id': claim.claim_id if claim else '',
    })


@require_GET
def chat_customer_messages(request):
    conversation = _get_or_create_customer_conversation(request)
    conversation.customer_unread = 0
    conversation.save()
    since_id = int(request.GET.get('since', 0))
    msgs = conversation.messages.filter(id__gt=since_id).order_by('created_at')
    return JsonResponse({
        'ok': True,
        'messages': [{
            'id': m.id,
            'sender': m.sender,
            'body': m.body,
            'attachment': m.attachment.url if m.attachment else '',
            'attachment_name': m.attachment.name.split('/')[-1] if m.attachment else '',
            'created_at': m.created_at.isoformat(),
        } for m in msgs],
    })


@require_POST
def chat_customer_send(request):
    conversation = _get_or_create_customer_conversation(request)
    body = (request.POST.get('body') or '').strip()
    attachment = request.FILES.get('attachment')
    if not body and not attachment:
        return JsonResponse({'ok': False, 'error': 'Empty message'}, status=400)
    ChatMessage.objects.create(
        conversation=conversation, sender='customer', body=body, attachment=attachment
    )
    conversation.staff_unread += 1
    conversation.save()
    return JsonResponse({'ok': True})


@staff_member_required
def chat_inbox_view(request):
    convs = ChatConversation.objects.all().order_by('-last_message_at')
    return render(request, 'store/chat_inbox.html', {'conversations': convs})


@staff_member_required
def chat_staff_thread(request, pk):
    conversation = get_object_or_404(ChatConversation, pk=pk)
    conversation.staff_unread = 0
    conversation.save()
    claim = _claim_for_conversation(conversation)
    return render(request, 'store/chat_staff_thread.html', {
        'conversation': conversation,
        'ice_servers': _ice_servers(),
        'call_claim_id': claim.claim_id if claim else '',
    })


@staff_member_required
@require_GET
def chat_staff_messages(request, pk):
    conversation = get_object_or_404(ChatConversation, pk=pk)
    conversation.staff_unread = 0
    conversation.save()
    since_id = int(request.GET.get('since', 0))
    msgs = conversation.messages.filter(id__gt=since_id).order_by('created_at')
    return JsonResponse({
        'ok': True,
        'messages': [{
            'id': m.id,
            'sender': m.sender,
            'body': m.body,
            'attachment': m.attachment.url if m.attachment else '',
            'attachment_name': m.attachment.name.split('/')[-1] if m.attachment else '',
            'created_at': m.created_at.isoformat(),
        } for m in msgs],
    })


@staff_member_required
@require_POST
def chat_staff_send(request, pk):
    conversation = get_object_or_404(ChatConversation, pk=pk)
    body = (request.POST.get('body') or '').strip()
    attachment = request.FILES.get('attachment')
    if not body and not attachment:
        return JsonResponse({'ok': False, 'error': 'Empty message'}, status=400)
    ChatMessage.objects.create(
        conversation=conversation, sender='staff', body=body, attachment=attachment
    )
    conversation.customer_unread += 1
    conversation.save()
    return JsonResponse({'ok': True})