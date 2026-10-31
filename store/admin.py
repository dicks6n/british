from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Count

from .models import (
    FlightClaim, ClaimEvidence, SupportCall, CallSignal,
    ChatConversation, ChatMessage,
)
from .eligibility import evaluate


# ── Inlines ───────────────────────────────────────────────
class ClaimEvidenceInline(admin.TabularInline):
    model = ClaimEvidence
    extra = 0
    fields = ('kind', 'file', 'note', 'uploaded_at')
    readonly_fields = ('uploaded_at',)


class CallSignalInline(admin.TabularInline):
    model = CallSignal
    extra = 0
    fields = ('sender', 'kind', 'payload', 'created_at')
    readonly_fields = ('created_at',)
    max_num = 0


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ('sender', 'body', 'attachment', 'created_at')
    readonly_fields = ('created_at',)
    max_num = 0


# ── FlightClaim Admin ────────────────────────────────────
@admin.register(FlightClaim)
class FlightClaimAdmin(admin.ModelAdmin):
    # List view
    list_display = (
        'claim_id_link', 'claim_type', 'status_badge', 'fullname',
        'route', 'flight_date', 'jurisdiction', 'amount',
        'has_evidence', 'created_at',
    )
    list_filter = (
        'claim_type', 'status', 'jurisdiction',
        'flight_date', 'created_at', 'airline_country',
    )
    search_fields = (
        'claim_id', 'fullname', 'email', 'phone',
        'booking_no', 'from_iata', 'to_iata',
        'from_name', 'to_name', 'pir_number',
    )
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 50
    inlines = [ClaimEvidenceInline]

    # Readonly fields (system-generated)
    readonly_fields = (
        'claim_id', 'created_at', 'updated_at',
        'jurisdiction', 'eligible_amount', 'currency_display',
        'eligibility_notes', 'decided_at',
        'route_distance_display', 'claim_summary',
    )

    # Complete fieldsets — every field from the home form, grouped clearly
    fieldsets = (
        ('🎫 Claim Identity', {
            'fields': ('claim_id', 'claim_summary', 'user', 'claim_type',
                       'status', 'created_at', 'updated_at'),
            'description': 'Public ID is auto-generated on first save (BA-XXXXXX).',
        }),
        ('👤 Passenger Contact', {
            'fields': ('fullname', 'email', 'phone'),
        }),
        ('✈️ Airline', {
            'fields': ('airline_iata', 'airline_name', 'airline_country'),
            'description': 'Airline IATA code (e.g. BA, TK, AC), name, and home country code.',
        }),
        ('🛫 Departure Airport', {
            'fields': ('from_iata', 'from_name', 'from_city',
                       'from_country', 'from_lat', 'from_lng'),
        }),
        ('🛬 Arrival Airport', {
            'fields': ('to_iata', 'to_name', 'to_city',
                       'to_country', 'to_lat', 'to_lng'),
        }),
        ('📅 Flight Details', {
            'fields': ('booking_no', 'flight_date', 'route_distance_display'),
        }),
        ('⏱️ Flight Delayed — only if claim_type = delayed', {
            'fields': ('delay_duration',),
            'classes': ('collapse',),
        }),
        ('❌ Flight Cancelled — only if claim_type = cancelled', {
            'fields': ('cancellation_notice', 'alternative_offered'),
            'classes': ('collapse',),
        }),
        ('🔁 Missed Connection — only if claim_type = connection', {
            'fields': ('conn_from', 'conn_middle', 'conn_to', 'connection_time'),
            'classes': ('collapse',),
        }),
        ('🚫 Denied Boarding — only if claim_type = denied', {
            'fields': ('denied_alternative',),
            'classes': ('collapse',),
        }),
        ('🧳 Baggage Claim — only if claim_type = baggage', {
            'fields': ('baggage_airport', 'pir_number', 'baggage_issue_type'),
            'classes': ('collapse',),
        }),
        ('⚖️ Eligibility Engine (auto-calculated)', {
            'fields': ('jurisdiction', 'eligible_amount', 'currency_display',
                       'eligibility_notes'),
            'description': 'Run the eligibility engine or approve the claim below.',
        }),
        ('✅ Staff Decision', {
            'fields': ('admin_decision_notes', 'decided_at'),
        }),
    )

    # Custom actions
    actions = ['rerun_eligibility', 'approve_selected', 'reject_selected']

    # ── Custom columns / displays ─────────────────────────
    def claim_id_link(self, obj):
        url = reverse('admin:store_flightclaim_change', args=[obj.pk])
        return format_html('<a href="{}"><strong>{}</strong></a>',
                           url, obj.claim_id or f'#{obj.pk}')
    claim_id_link.short_description = 'Claim ID'
    claim_id_link.admin_order_field = 'claim_id'

    def status_badge(self, obj):
        colors = {
            'pending': '#fef3c7', 'eligible': '#dbeafe',
            'approved': '#dcfce7', 'rejected': '#fee2e2',
            'cancelled': '#f3f4f6',
        }
        text_colors = {
            'pending': '#92400e', 'eligible': '#1e40af',
            'approved': '#166534', 'rejected': '#991b1b',
            'cancelled': '#4b5563',
        }
        bg = colors.get(obj.status, '#f3f4f6')
        fg = text_colors.get(obj.status, '#333')
        return format_html(
            '<span style="background:{};color:{};padding:3px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            bg, fg, obj.get_status_display(),
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def route(self, obj):
        if obj.from_iata or obj.to_iata:
            return f"{obj.from_iata or '?'} → {obj.to_iata or '?'}"
        return '—'
    route.short_description = 'Route'

    def amount(self, obj):
        if obj.eligible_amount:
            return f"€{obj.eligible_amount}"
        return '—'
    amount.short_description = 'Eligible'
    amount.admin_order_field = 'eligible_amount'

    def has_evidence(self, obj):
        n = obj.evidence.count()
        if n:
            return format_html(
                '<span style="color:#166534;font-weight:600;">✓ {} file{}</span>',
                n, '' if n == 1 else 's',
            )
        return format_html('<span style="color:#9ca3af;">—</span>')
    has_evidence.short_description = 'Evidence'

    def route_distance_display(self, obj):
        km = obj.route_distance_km()
        if km:
            return f"{km:,.0f} km"
        return '— (missing airport coordinates)'
    route_distance_display.short_description = 'Route distance'

    def claim_summary(self, obj):
        if not obj.pk:
            return '—'
        return format_html(
            '<div style="padding:12px;background:#f8fafc;'
            'border-left:4px solid #1b6ca8;border-radius:4px;">'
            '<strong>{}</strong> · {} · {} · {}<br>'
            '<span style="color:#64748b;font-size:12px;">'
            'Created {} · Last updated {}'
            '</span></div>',
            obj.claim_id or '—',
            obj.get_claim_type_display(),
            obj.fullname or 'Guest',
            obj.route(),
            obj.created_at.strftime('%b %d, %Y · %H:%M'),
            obj.updated_at.strftime('%b %d, %Y · %H:%M'),
        )
    claim_summary.short_description = 'Summary'

    # ── Actions ───────────────────────────────────────────
    def rerun_eligibility(self, request, queryset):
        updated = 0
        for claim in queryset:
            result = evaluate(claim)
            for k, v in result.to_claim_fields().items():
                setattr(claim, k, v)
            claim.save()
            updated += 1
        self.message_user(request, f"Re-ran eligibility on {updated} claim(s).")
    rerun_eligibility.short_description = "⚖️  Re-run eligibility engine"

    def approve_selected(self, request, queryset):
        n = queryset.update(status='approved', decided_at=timezone.now())
        self.message_user(request, f"Approved {n} claim(s).")
    approve_selected.short_description = "✅  Approve selected claims"

    def reject_selected(self, request, queryset):
        n = queryset.update(status='rejected', decided_at=timezone.now())
        self.message_user(request, f"Rejected {n} claim(s).")
    reject_selected.short_description = "❌  Reject selected claims"


# ── SupportCall Admin ────────────────────────────────────
@admin.register(SupportCall)
class SupportCallAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'claim_id_display', 'customer_name', 'status_badge',
        'agent', 'initiated_by', 'duration', 'started_at',
    )
    list_filter = ('status', 'initiated_by', 'started_at')
    search_fields = ('customer_name', 'claim__claim_id', 'claim__fullname')
    date_hierarchy = 'started_at'
    ordering = ('-started_at',)
    inlines = [CallSignalInline]
    readonly_fields = (
        'id', 'claim', 'customer_name', 'status', 'agent',
        'initiated_by', 'ended_by', 'offer', 'answer',
        'started_at', 'accepted_at', 'ended_at', 'duration_seconds',
        'customer_seen_at', 'agent_seen_at',
    )

    fieldsets = (
        ('📞 Call', {
            'fields': ('id', 'claim', 'customer_name', 'agent',
                       'status', 'initiated_by', 'ended_by'),
        }),
        ('⏱️ Timing', {
            'fields': ('started_at', 'accepted_at', 'ended_at', 'duration_seconds',
                       'customer_seen_at', 'agent_seen_at'),
        }),
        ('📡 WebRTC Signalling', {
            'fields': ('offer', 'answer'),
            'classes': ('collapse',),
            'description': 'Raw SDP blobs used to negotiate the peer-to-peer connection.',
        }),
    )

    def claim_id_display(self, obj):
        if obj.claim:
            return format_html(
                '<a href="{}">{}</a>',
                reverse('admin:store_flightclaim_change', args=[obj.claim.pk]),
                obj.claim.claim_id,
            )
        return format_html('<span style="color:#9ca3af;">—</span>')
    claim_id_display.short_description = 'Claim'

    def status_badge(self, obj):
        colors = {
            'ringing': ('#fef3c7', '#92400e'),
            'connected': ('#dcfce7', '#166534'),
            'ended': ('#f3f4f6', '#4b5563'),
            'declined': ('#fee2e2', '#991b1b'),
            'missed': ('#ffedd5', '#9a3412'),
        }
        bg, fg = colors.get(obj.status, ('#f3f4f6', '#333'))
        return format_html(
            '<span style="background:{};color:{};padding:3px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            bg, fg, obj.get_status_display(),
        )
    status_badge.short_description = 'Status'

    def duration(self, obj):
        secs = obj.duration_seconds
        if secs is not None:
            return f"{secs}s"
        return '—'
    duration.short_description = 'Duration'


# ── Chat Admin ───────────────────────────────────────────
@admin.register(ChatConversation)
class ChatConversationAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'display_name', 'user', 'staff_unread',
        'customer_unread', 'last_message_at', 'closed',
    )
    list_filter = ('closed', 'created_at')
    search_fields = ('user__username', 'guest_name', 'guest_email')
    ordering = ('-last_message_at',)
    inlines = [ChatMessageInline]

    fieldsets = (
        ('👤 Identity', {
            'fields': ('user', 'guest_name', 'guest_email'),
        }),
        ('📊 Status', {
            'fields': ('staff_unread', 'customer_unread', 'closed',
                       'created_at', 'last_message_at'),
        }),
    )
    readonly_fields = ('created_at', 'last_message_at')


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'sender', 'short_body',
                    'has_attachment', 'created_at')
    list_filter = ('sender', 'created_at')
    search_fields = ('body', 'conversation__user__username')
    ordering = ('-created_at',)
    readonly_fields = ('conversation', 'sender', 'body',
                       'attachment', 'created_at')

    def short_body(self, obj):
        return obj.body[:60] if obj.body else '—'
    short_body.short_description = 'Message'

    def has_attachment(self, obj):
        if obj.attachment:
            return format_html('📎 Yes')
        return '—'
    has_attachment.short_description = 'Attachment'


# ── Customise the admin site branding ────────────────────
admin.site.site_header = "British Airways — Admin"
admin.site.site_title = "BA Admin"
admin.site.index_title = "Compensation Claims & Support"