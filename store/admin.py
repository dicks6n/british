from django.contrib import admin
from .models import FlightClaim

@admin.register(FlightClaim)
class FlightClaimAdmin(admin.ModelAdmin):
    list_display = ('id', 'claim_type', 'fullname', 'from_airport', 'to_airport', 'flight_date', 'status', 'approved_amount', 'created_at')
    list_filter = ('claim_type', 'status', 'flight_date', 'created_at')
    search_fields = ('fullname', 'email', 'booking_no', 'from_airport', 'to_airport', 'pir_number')
    list_editable = ('status', 'approved_amount')
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Overview', {
            'fields': ('user', 'claim_type', 'status', 'approved_amount', 'booking_no', 'flight_date', 'created_at')
        }),
        ('Passenger Contact Details', {
            'fields': ('fullname', 'email', 'phone')
        }),
        ('Flight Delayed Details', {
            'fields': ('from_airport', 'to_airport', 'delay_duration')
        }),
        ('Flight Cancelled Details', {
            'fields': ('cancellation_notice', 'alternative_offered')
        }),
        ('Missed Connection Details', {
            'fields': ('conn_from', 'conn_middle', 'conn_to', 'connection_time')
        }),
        ('Denied Boarding Details', {
            'fields': ('denied_alternative',)
        }),
        ('Baggage Claim Details', {
            'fields': ('baggage_airport', 'pir_number', 'baggage_issue_type')
        }),
    )