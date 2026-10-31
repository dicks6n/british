"""British Airways — flight compensation, support calls, and chat."""
import secrets
from django.conf import settings
from django.db import models
from django.contrib.auth.models import User


def generate_claim_id():
    return 'BA-' + secrets.token_hex(3).upper()


# ============================================================
# CLAIM
# ============================================================
class FlightClaim(models.Model):
    CLAIM_TYPES = [
        ('delayed', 'Flight Delayed'),
        ('cancelled', 'Flight Cancelled'),
        ('connection', 'Missed Connection'),
        ('denied', 'Denied Boarding'),
        ('baggage', 'Baggage Claim'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending review'),
        ('eligible', 'Eligible — awaiting approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]
    JURISDICTIONS = [
        ('EU261', 'EU Regulation 261/2004'),
        ('TR_SHY', 'Turkish SHY Passenger Rights'),
        ('CA_APPR', 'Canada Air Passenger Protection Regulations'),
        ('NONE', 'Not covered by EU/TR/CA rules'),
    ]

    claim_id = models.CharField(
        max_length=20, unique=True, blank=True, null=True,
        help_text="Short public ID for the customer (BA-XXXXXX).",
    )

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    claim_type = models.CharField(max_length=20, choices=CLAIM_TYPES, default='delayed')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    fullname = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)

    airline_iata    = models.CharField(max_length=3, blank=True)
    airline_name    = models.CharField(max_length=255, blank=True)
    airline_country = models.CharField(max_length=2, blank=True)

    from_iata    = models.CharField(max_length=4, blank=True)
    from_name    = models.CharField(max_length=255, blank=True)
    from_city    = models.CharField(max_length=120, blank=True)
    from_country = models.CharField(max_length=2, blank=True)
    from_lat     = models.FloatField(null=True, blank=True)
    from_lng     = models.FloatField(null=True, blank=True)

    to_iata      = models.CharField(max_length=4, blank=True)
    to_name      = models.CharField(max_length=255, blank=True)
    to_city      = models.CharField(max_length=120, blank=True)
    to_country   = models.CharField(max_length=2, blank=True)
    to_lat       = models.FloatField(null=True, blank=True)
    to_lng       = models.FloatField(null=True, blank=True)

    booking_no  = models.CharField(max_length=100, blank=True, null=True)
    flight_date = models.DateField(blank=True, null=True)

    delay_duration       = models.CharField(max_length=100, blank=True, null=True)
    cancellation_notice  = models.CharField(max_length=100, blank=True, null=True)
    alternative_offered  = models.CharField(max_length=50, blank=True, null=True)
    conn_from            = models.CharField(max_length=255, blank=True, null=True)
    conn_middle          = models.CharField(max_length=255, blank=True, null=True)
    conn_to              = models.CharField(max_length=255, blank=True, null=True)
    connection_time      = models.CharField(max_length=100, blank=True, null=True)
    denied_alternative   = models.CharField(max_length=50, blank=True, null=True)
    baggage_airport      = models.CharField(max_length=255, blank=True, null=True)
    pir_number           = models.CharField(max_length=100, blank=True, null=True)
    baggage_issue_type   = models.CharField(max_length=100, blank=True, null=True)

    jurisdiction       = models.CharField(max_length=20, choices=JURISDICTIONS, default='NONE')
    eligible_amount    = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    currency_display   = models.CharField(max_length=8, default='EUR')
    eligibility_notes  = models.TextField(blank=True)

    admin_decision_notes = models.TextField(blank=True)
    decided_at           = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.claim_id or f'#{self.pk}'} — {self.get_claim_type_display()} — {self.fullname or 'Guest'}"

    def save(self, *args, **kwargs):
        if not self.claim_id:
            for _ in range(20):
                cid = generate_claim_id()
                if not FlightClaim.objects.filter(claim_id=cid).exists():
                    self.claim_id = cid
                    break
        super().save(*args, **kwargs)

    def route_distance_km(self):
        from .eligibility import great_circle_km
        if None in (self.from_lat, self.from_lng, self.to_lat, self.to_lng):
            return None
        return great_circle_km(
            (self.from_lat, self.from_lng),
            (self.to_lat, self.to_lng),
        )


class ClaimEvidence(models.Model):
    KINDS = [
        ('boarding_pass', 'Boarding pass'),
        ('booking_conf', 'Booking confirmation'),
        ('delay_notice', 'Airline delay / cancellation notice'),
        ('pir', 'Property Irregularity Report (PIR)'),
        ('receipt', 'Receipt for expenses'),
        ('other', 'Other'),
    ]
    claim       = models.ForeignKey(FlightClaim, on_delete=models.CASCADE, related_name='evidence')
    kind        = models.CharField(max_length=30, choices=KINDS, default='other')
    file        = models.FileField(upload_to='evidence/%Y/%m/')
    note        = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']


# ============================================================
# SUPPORT CALL
# ============================================================
class SupportCall(models.Model):
    STATUS = [
        ('ringing', 'Ringing'),
        ('connected', 'Connected'),
        ('ended', 'Ended'),
        ('declined', 'Declined'),
        ('missed', 'Missed'),
    ]
    OPEN_STATUSES = ('ringing', 'connected')
    SIDE_CHOICES = [('customer', 'Customer'), ('agent', 'Agent')]

    customer_name = models.CharField(max_length=255, blank=True, default='Guest')
    status = models.CharField(max_length=20, choices=STATUS, default='ringing')

    claim = models.ForeignKey(
        FlightClaim, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='support_calls',
    )
    agent = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='support_calls',
    )
    initiated_by = models.CharField(max_length=10, choices=SIDE_CHOICES, default='customer')
    ended_by     = models.CharField(max_length=10, choices=SIDE_CHOICES, blank=True)

    offer  = models.TextField(blank=True, default='')
    answer = models.TextField(blank=True, default='')

    started_at  = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    ended_at    = models.DateTimeField(null=True, blank=True)

    # Heartbeats, updated each time a side polls the call (see views._expire_ringing).
    customer_seen_at = models.DateTimeField(null=True, blank=True)
    agent_seen_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"Call #{self.pk} — {self.customer_name} ({self.status})"

    @property
    def duration_seconds(self):
        if self.accepted_at and self.ended_at:
            return int((self.ended_at - self.accepted_at).total_seconds())
        return None

    def to_dict(self, include_sdp=False):
        data = {
            'id': self.pk,
            'claim_id': self.claim.claim_id if self.claim else None,
            'customer_name': self.customer_name,
            'agent_name': (self.agent.get_full_name() or self.agent.username) if self.agent else None,
            'initiated_by': self.initiated_by,
            'ended_by': self.ended_by,
            'status': self.status,
            'accepted_at': self.accepted_at.isoformat() if self.accepted_at else None,
            'started_at': self.started_at.isoformat(),
        }
        if include_sdp:
            data['offer']  = self.offer  or ''
            data['answer'] = self.answer or ''
        return data


class CallSignal(models.Model):
    SENDERS = [('customer', 'customer'), ('agent', 'agent')]
    KINDS   = [('offer', 'offer'), ('answer', 'answer'), ('ice', 'ice'), ('bye', 'bye')]

    call       = models.ForeignKey(SupportCall, on_delete=models.CASCADE, related_name='signals')
    sender     = models.CharField(max_length=10, choices=SENDERS)
    kind       = models.CharField(max_length=10, choices=KINDS)
    payload    = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']


# ============================================================
# CHAT
# ============================================================
class ChatConversation(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='chat_conversations',
    )
    guest_name = models.CharField(max_length=255, blank=True, null=True)
    guest_email = models.EmailField(blank=True, null=True)
    staff_unread = models.PositiveIntegerField(default=0)
    customer_unread = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-last_message_at']

    def __str__(self):
        return f"Conversation #{self.id} — {self.display_name}"

    @property
    def display_name(self):
        if self.user:
            return self.user.get_full_name() or self.user.username
        return self.guest_name or 'Guest'

    @property
    def last_preview(self):
        last = self.messages.last()
        if last:
            return last.body[:80] if last.body else '📎 Attachment'
        return 'No messages yet.'


class ChatMessage(models.Model):
    SENDERS = [('customer', 'Customer'), ('staff', 'Staff')]

    conversation = models.ForeignKey(
        ChatConversation, on_delete=models.CASCADE, related_name='messages',
    )
    sender = models.CharField(max_length=10, choices=SENDERS)
    body = models.TextField(blank=True)
    attachment = models.FileField(upload_to='chat/%Y/%m/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"#{self.id} ({self.sender}) — {self.body[:30]}"