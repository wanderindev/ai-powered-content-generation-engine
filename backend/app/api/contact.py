import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.contact_submission import ContactSubmission
from app.schemas.contact import ContactFormRequest, ContactFormResponse
from app.services.email_sender import EmailDeliveryError, send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contact", tags=["contact"])


def _extra_form_data(form: ContactFormRequest) -> dict:
    """Extract type-specific fields into a JSON-serialisable dict."""
    if form.interest == "field-trips":
        return {
            k: v
            for k, v in {
                "school_name": form.school_name,
                "grade_levels": form.grade_levels,
                "field_trips": form.field_trips,
            }.items()
            if v
        }
    if form.interest == "tours":
        return {
            k: v
            for k, v in {
                "country": form.country,
                "date_from": form.date_from,
                "date_to": form.date_to,
                "adults": form.adults,
                "children_under_5": form.children_under_5,
                "children_5_to_17": form.children_5_to_17,
                "tours": form.tours,
                "hotel_address": form.hotel_address,
                "needs_transport": form.needs_transport,
            }.items()
            if v
        }
    return {}


def _format_contact_email(form: ContactFormRequest) -> str:
    """Format contact form data as an HTML email body."""
    rows = f"""
    <tr><td style="padding:8px; font-weight:bold;">Interest</td><td style="padding:8px;">{form.interest}</td></tr>
    <tr><td style="padding:8px; font-weight:bold;">Name</td><td style="padding:8px;">{form.first_name} {form.last_name}</td></tr>
    <tr><td style="padding:8px; font-weight:bold;">Email</td><td style="padding:8px;">{form.email}</td></tr>
    """

    if form.interest == "field-trips":
        if form.school_name:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">School</td><td style="padding:8px;">{form.school_name}</td></tr>'
        if form.grade_levels:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Grade Levels</td><td style="padding:8px;">{", ".join(form.grade_levels)}</td></tr>'
        if form.field_trips:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Field Trips</td><td style="padding:8px;">{", ".join(form.field_trips)}</td></tr>'
    elif form.interest == "tours":
        if form.country:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Country</td><td style="padding:8px;">{form.country}</td></tr>'
        if form.date_from or form.date_to:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Dates</td><td style="padding:8px;">{form.date_from or "?"} to {form.date_to or "?"}</td></tr>'
        if form.adults:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Adults</td><td style="padding:8px;">{form.adults}</td></tr>'
        if form.children_under_5:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Children under 5</td><td style="padding:8px;">{form.children_under_5}</td></tr>'
        if form.children_5_to_17:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Children 5-17</td><td style="padding:8px;">{form.children_5_to_17}</td></tr>'
        if form.tours:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Tours</td><td style="padding:8px;">{", ".join(form.tours)}</td></tr>'
        if form.hotel_address:
            rows += f'<tr><td style="padding:8px; font-weight:bold;">Hotel</td><td style="padding:8px;">{form.hotel_address}</td></tr>'
        if form.needs_transport:
            rows += '<tr><td style="padding:8px; font-weight:bold;">Transport</td><td style="padding:8px;">Requested</td></tr>'

    message_html = ""
    if form.message:
        message_html = f'<h3>Message</h3><p style="white-space: pre-wrap;">{form.message}</p>'

    return f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <div style="max-width: 600px; margin: 0 auto;">
        <h2 style="color: #1095d3;">New Contact Form Submission</h2>
        <table style="width: 100%; border-collapse: collapse;">
            {rows}
        </table>
        {message_html}
    </div>
    </body></html>
    """


@router.post("", response_model=ContactFormResponse)
def submit_contact_form(
    form: ContactFormRequest,
    db: Session = Depends(get_db),
) -> ContactFormResponse:
    """
    Submit a contact form inquiry.

    Persists the submission to the database, then attempts to send
    a formatted HTML email to the admin notification address via the
    SMTP relay. The submission is saved even if email delivery fails;
    the failure is recorded on the row for an audit trail.
    """
    settings = get_settings()

    # Persist submission first — never lose form data
    submission = ContactSubmission(
        interest=form.interest,
        first_name=form.first_name,
        last_name=form.last_name,
        email=form.email,
        message=form.message,
        form_data=_extra_form_data(form),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    # Attempt email notification (non-fatal: form data persists either way).
    interest_label = "Field Trip" if form.interest == "field-trips" else "Tour"
    subject = f"[Contact Form] {interest_label} Inquiry - {form.first_name} {form.last_name}"
    body = _format_contact_email(form)

    try:
        send_email(settings.admin_notification_email, subject, body)
        submission.email_sent = True
    except EmailDeliveryError as e:
        submission.email_error = str(e)
        logger.error("Contact submission %d saved but email failed: %s", submission.id, e)

    db.commit()

    return ContactFormResponse(
        success=True,
        message="Your message has been sent successfully. We'll be in touch soon!",
    )
