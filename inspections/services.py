"""
Inspection business rules.

Life cycle:  ASSIGNED --start--> IN_PROGRESS --save progress (any number of times)--> --submit--> SUBMITTED
Submitted inspections are immutable: a correction means a new inspection.
"""
import io

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from accounts.models import Role
from core.permissions import get_role
from equipment.services import recompute_health

from .models import Defect, Inspection, InspectionResponse, InspectionTemplate

Outcome = InspectionResponse.Outcome

ALLOWED_PHOTO_FORMATS = {"JPEG", "PNG", "WEBP"}
REMARKS_REQUIRED_FOR = {Outcome.FAIL, Outcome.NEEDS_ATTENTION}
DEFECT_SEVERITY_FOR = {Outcome.FAIL: Defect.Severity.HIGH, Outcome.NEEDS_ATTENTION: Defect.Severity.MEDIUM}


class InspectionError(Exception):
    """An inspection action that is not allowed in the inspection's current state."""


# --- The result rule -----------------------------------------------------------------


def overall_result(outcomes):
    """
    Any FAIL -> FAIL; otherwise any NEEDS_ATTENTION -> ATTENTION; otherwise PASS.
    N/A answers are ignored. All N/A (nothing actually checked) is not allowed.
    """
    counted = [outcome for outcome in outcomes if outcome != Outcome.NOT_APPLICABLE]
    if not counted:
        raise ValidationError("At least one item must be checked; all items are N/A.")
    if Outcome.FAIL in counted:
        return Inspection.Result.FAIL
    if Outcome.NEEDS_ATTENTION in counted:
        return Inspection.Result.ATTENTION
    return Inspection.Result.PASS


def response_errors(responses):
    """Problems that block submission, as readable messages."""
    errors = []
    for number, response in enumerate(responses, start=1):
        if not response.outcome:
            errors.append(f"Item {number} has no answer.")
        elif response.outcome in REMARKS_REQUIRED_FOR and not response.remarks.strip():
            errors.append(f"Item {number}: remarks are required for '{response.get_outcome_display()}'.")
    return errors


# --- Creating and starting ----------------------------------------------------------------


def snapshot_items(inspection):
    """Copy the template's checklist into response rows, so later template edits don't change history."""
    items = list(inspection.template.items.all())
    if not items:
        raise InspectionError("The checklist template has no items.")
    InspectionResponse.objects.bulk_create(
        InspectionResponse(inspection=inspection, order=item.order, item_text=item.text) for item in items
    )


def assign_inspection(*, equipment, template, inspector, due_date, created_by):
    if not equipment.is_active:
        raise InspectionError(f"{equipment.equipment_code} is deactivated.")
    if not template.is_active:
        raise InspectionError("That checklist template is inactive.")
    return Inspection.objects.create(
        equipment=equipment,
        template=template,
        inspector=inspector,
        created_by=created_by,
        due_date=due_date,
        status=Inspection.Status.ASSIGNED,
    )


def start_inspection(inspection, user):
    """ASSIGNED -> IN_PROGRESS (assignee only). Starting twice is harmless."""
    with transaction.atomic():
        inspection = Inspection.objects.select_for_update().get(pk=inspection.pk)
        if inspection.inspector_id != user.pk:
            raise PermissionDenied("Only the assigned inspector can start this inspection.")
        if inspection.status == Inspection.Status.SUBMITTED:
            raise InspectionError("This inspection has already been submitted.")
        if inspection.status == Inspection.Status.ASSIGNED:
            inspection.status = Inspection.Status.IN_PROGRESS
            inspection.started_at = timezone.now()
            inspection.save(update_fields=["status", "started_at"])
            snapshot_items(inspection)
    return inspection


def adhoc_blocker(equipment, user):
    """Why `user` cannot start an ad-hoc inspection on `equipment` (None if they can)."""
    if not settings.ALLOW_ADHOC_INSPECTIONS:
        return "Ad-hoc inspections are turned off."
    if get_role(user) != Role.INSPECTOR:
        return "Only inspectors start ad-hoc inspections."
    if not equipment.is_active:
        return f"{equipment.equipment_code} is deactivated."
    if Inspection.objects.open().filter(equipment=equipment, inspector=user).exists():
        return "You already have an open inspection for this machine."
    if InspectionTemplate.objects.template_for(equipment) is None:
        return "There is no active checklist for this type of machine."
    return None


def start_adhoc_inspection(equipment, user):
    """An inspector starts an unassigned inspection from the equipment page (or after scanning its QR)."""
    blocker = adhoc_blocker(equipment, user)
    if blocker:
        if get_role(user) != Role.INSPECTOR:
            raise PermissionDenied(blocker)
        raise InspectionError(blocker)
    with transaction.atomic():
        inspection = Inspection.objects.create(
            equipment=equipment,
            template=InspectionTemplate.objects.template_for(equipment),
            inspector=user,
            created_by=user,
            status=Inspection.Status.IN_PROGRESS,
            started_at=timezone.now(),
        )
        snapshot_items(inspection)
    return inspection


# --- Saving and submitting --------------------------------------------------------------------


def save_progress(inspection, user, response_formset, remarks_form):
    """
    Store the inspector's answers (and photos) without submitting.
    The row lock + status check stops a save landing after a submit.
    """
    with transaction.atomic():
        locked = Inspection.objects.select_for_update().get(pk=inspection.pk)
        if locked.inspector_id != user.pk:
            raise PermissionDenied("Only the assigned inspector can fill in this inspection.")
        if locked.status != Inspection.Status.IN_PROGRESS:
            raise InspectionError("This inspection is not in progress (it may already be submitted).")
        response_formset.save()
        remarks_form.save()


def submit_inspection(inspection, user):
    """
    IN_PROGRESS -> SUBMITTED, all in one transaction:
    validate every answer, set the result, create defects, recompute equipment health.
    Locking the row and re-checking the status makes a double submit fail safely.
    """
    with transaction.atomic():
        inspection = Inspection.objects.select_for_update().select_related("equipment").get(pk=inspection.pk)
        if inspection.status == Inspection.Status.SUBMITTED:
            raise InspectionError("This inspection has already been submitted.")
        if inspection.inspector_id != user.pk:
            raise PermissionDenied("Only the assigned inspector can submit this inspection.")
        if inspection.status != Inspection.Status.IN_PROGRESS:
            raise InspectionError("Start the inspection before submitting it.")

        responses = list(inspection.responses.all())
        errors = response_errors(responses)
        if errors:
            raise ValidationError(errors)
        inspection.overall_result = overall_result([r.outcome for r in responses])
        inspection.status = Inspection.Status.SUBMITTED
        inspection.submitted_at = timezone.now()
        inspection.save(update_fields=["overall_result", "status", "submitted_at"])

        Defect.objects.bulk_create(
            Defect(
                equipment=inspection.equipment,
                inspection=inspection,
                response=response,
                description=f"{response.item_text} - {response.remarks.strip()}",
                severity=DEFECT_SEVERITY_FOR[response.outcome],
            )
            for response in responses
            if response.outcome in DEFECT_SEVERITY_FOR
        )
        recompute_health(inspection.equipment)
    return inspection


# --- Photos ------------------------------------------------------------------------------------


def process_photo(uploaded_file):
    """
    Validate an uploaded photo and return a clean JPEG (as ContentFile).

    - max 5 MB, and must really be a JPEG/PNG/WebP (checked by Pillow, not the file name)
    - rotated upright using its EXIF orientation, then EXIF is dropped (no GPS/camera data kept)
    - resized so the longest side is at most 1600 px
    """
    if uploaded_file.size > settings.PHOTO_MAX_UPLOAD_BYTES:
        raise ValidationError("The photo is larger than 5 MB.")
    try:
        image = Image.open(uploaded_file)
        image.verify()  # detects truncated or fake files
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ValidationError("This file is not a valid image.")
    if image.format not in ALLOWED_PHOTO_FORMATS:
        raise ValidationError("Only JPEG, PNG or WebP photos are allowed.")

    uploaded_file.seek(0)
    image = Image.open(uploaded_file)  # verify() leaves the image unusable, so open it again
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((settings.PHOTO_MAX_DIMENSION, settings.PHOTO_MAX_DIMENSION))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)  # no exif= argument -> metadata stripped
    return ContentFile(buffer.getvalue(), name="photo.jpg")
