from django.db import models
from django.contrib.auth.models import User

class FlightClaim(models.Model):
    CLAIM_TYPES = [
        ('delayed', 'Flight Delayed'),
        ('cancelled', 'Flight Cancelled'),
        ('connection', 'Missed Connection'),
        ('denied', 'Denied Boarding'),
        ('baggage', 'Baggage Claim'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    claim_type = models.CharField(max_length=20, choices=CLAIM_TYPES, default='delayed')
    
    # Common Fields
    booking_no = models.CharField(max_length=100, blank=True, null=True)
    flight_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Status and Contact Fields
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fullname = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)

    # Flight Delayed Fields
    from_airport = models.CharField(max_length=255, blank=True, null=True)
    to_airport = models.CharField(max_length=255, blank=True, null=True)
    delay_duration = models.CharField(max_length=100, blank=True, null=True)

    # Flight Cancelled Fields
    cancellation_notice = models.CharField(max_length=100, blank=True, null=True)
    alternative_offered = models.CharField(max_length=50, blank=True, null=True)

    # Missed Connection Fields
    conn_from = models.CharField(max_length=255, blank=True, null=True)
    conn_middle = models.CharField(max_length=255, blank=True, null=True)
    conn_to = models.CharField(max_length=255, blank=True, null=True)
    connection_time = models.CharField(max_length=100, blank=True, null=True)

    # Denied Boarding Fields
    denied_alternative = models.CharField(max_length=50, blank=True, null=True)

    # Baggage Claim Fields
    baggage_airport = models.CharField(max_length=255, blank=True, null=True)
    pir_number = models.CharField(max_length=100, blank=True, null=True)
    baggage_issue_type = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.get_claim_type_display()} - {self.booking_no or 'No Booking No'} ({self.created_at.strftime('%Y-%m-%d')})"