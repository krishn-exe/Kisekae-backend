import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from stores.models import StoreMembership, StoreRole, VerificationStatus

logger = logging.getLogger(__name__)


class NotificationEmailService:
    @staticmethod
    def send_multipart_email(to_email, subject, template_name, context, plain_text_fallback=None):
        """
        Renders responsive HTML template and sends multi-part email with plain text alternative.
        """
        if not to_email:
            return False

        try:
            html_content = render_to_string(template_name, context)
            text_content = plain_text_fallback or strip_tags(html_content).strip()

            email_msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[to_email],
            )
            email_msg.attach_alternative(html_content, "text/html")
            email_msg.send(fail_silently=False)
            logger.info("Email '%s' sent successfully to %s", subject, to_email)
            return True
        except Exception as exc:
            logger.error("Failed to send email '%s' to %s: %s", subject, to_email, exc, exc_info=True)
            raise

    @staticmethod
    def send_order_lifecycle_email(order, event_type, notes=""):
        """
        Dispatches customer order notifications (CONFIRMED, SHIPPED, DELIVERED, CANCELLED).
        Respects customer's notification preferences if registered user.
        """
        user = order.user
        if user and hasattr(user, "settings"):
            if not user.settings.email_notifications or not user.settings.order_alerts:
                logger.info("Order lifecycle email skipped for user %s per notification settings", user.email)
                return False

        templates = {
            "CONFIRMED": ("emails/order_confirmed.html", f"Order Confirmed: #{order.order_number}"),
            "SHIPPED": ("emails/order_shipped.html", f"Your Order #{order.order_number} Has Shipped!"),
            "OUT_FOR_DELIVERY": ("emails/order_out_for_delivery.html", f"Out for Delivery: Order #{order.order_number}"),
            "DELIVERED": ("emails/order_delivered.html", f"Delivered: Your Kisekae Order #{order.order_number}"),
            "CANCELLED": ("emails/order_cancelled.html", f"Order Cancelled: #{order.order_number}"),
        }

        template_name, subject = templates.get(
            event_type,
            ("emails/order_confirmed.html", f"Order Update: #{order.order_number}")
        )

        context = {
            "order": order,
            "notes": notes,
        }

        return NotificationEmailService.send_multipart_email(
            to_email=order.customer_email,
            subject=subject,
            template_name=template_name,
            context=context,
        )

    @staticmethod
    def send_seller_new_order_alert(order):
        """
        Dispatches new order alert to all Store Owners and Managers who have order_alerts enabled.
        """
        store = order.store
        memberships = (
            StoreMembership.objects.filter(
                store=store,
                role__in=[StoreRole.OWNER, StoreRole.MANAGER],
            )
            .select_related("user")
        )

        sent_count = 0
        subject = f"[{store.name}] New Order Received: #{order.order_number}"
        context = {
            "order": order,
        }

        for membership in memberships:
            member_user = membership.user
            if not member_user or not member_user.email:
                continue

            if hasattr(member_user, "settings"):
                if not member_user.settings.email_notifications or not member_user.settings.order_alerts:
                    logger.info("Seller order alert skipped for member %s per preferences", member_user.email)
                    continue

            NotificationEmailService.send_multipart_email(
                to_email=member_user.email,
                subject=subject,
                template_name="emails/seller_new_order.html",
                context=context,
            )
            sent_count += 1

        return sent_count

    @staticmethod
    def send_seller_return_requested_alert(order):
        """
        Dispatches return requested alert to Store Owners and Managers who have order_alerts enabled.
        """
        store = order.store
        memberships = (
            StoreMembership.objects.filter(
                store=store,
                role__in=[StoreRole.OWNER, StoreRole.MANAGER],
            )
            .select_related("user")
        )

        sent_count = 0
        subject = f"[{store.name}] Return Requested: Order #{order.order_number}"
        context = {
            "order": order,
        }

        for membership in memberships:
            member_user = membership.user
            if not member_user or not member_user.email:
                continue

            if hasattr(member_user, "settings"):
                if not member_user.settings.email_notifications or not member_user.settings.order_alerts:
                    logger.info("Seller return alert skipped for member %s per preferences", member_user.email)
                    continue

            NotificationEmailService.send_multipart_email(
                to_email=member_user.email,
                subject=subject,
                template_name="emails/seller_return_requested.html",
                context=context,
            )
            sent_count += 1

        return sent_count

    @staticmethod
    def send_store_verification_submitted_email(verification):
        """
        Sends confirmation email STRICTLY to the store owner when verification is submitted.
        """
        store = verification.store
        owner_membership = (
            StoreMembership.objects.filter(store=store, role=StoreRole.OWNER)
            .select_related("user")
            .first()
        )
        owner_user = (owner_membership.user if owner_membership else None) or store.created_by
        to_email = owner_user.email if owner_user and owner_user.email else verification.business_email

        if not to_email:
            logger.warning("No recipient found for store %s verification submission email", store.id)
            return False

        subject = f"[{store.name}] Verification Application Received"
        context = {
            "store": store,
            "verification": verification,
            "owner": owner_user,
        }

        return NotificationEmailService.send_multipart_email(
            to_email=to_email,
            subject=subject,
            template_name="emails/seller_verification_submitted.html",
            context=context,
        )

    @staticmethod
    def send_store_verification_result_email(verification):
        """
        Sends verification approval or rejection email STRICTLY to the store owner.
        """
        store = verification.store
        owner_membership = (
            StoreMembership.objects.filter(store=store, role=StoreRole.OWNER)
            .select_related("user")
            .first()
        )
        owner_user = (owner_membership.user if owner_membership else None) or store.created_by
        to_email = owner_user.email if owner_user and owner_user.email else verification.business_email

        if not to_email:
            logger.warning("No recipient found for store %s verification result email", store.id)
            return False

        context = {
            "store": store,
            "verification": verification,
            "rejection_reason": verification.rejection_reason,
            "owner": owner_user,
        }

        if verification.status == VerificationStatus.APPROVED:
            subject = f"[{store.name}] Business Verification Approved!"
            template_name = "emails/seller_verification_approved.html"
        elif verification.status == VerificationStatus.REJECTED:
            subject = f"[{store.name}] Business Verification Update: Not Approved"
            template_name = "emails/seller_verification_rejected.html"
        else:
            logger.info("Verification result email skipped for status '%s'", verification.status)
            return False

        return NotificationEmailService.send_multipart_email(
            to_email=to_email,
            subject=subject,
            template_name=template_name,
            context=context,
        )

    @staticmethod
    def send_seller_low_stock_total_alert(product, current_stock):
        """
        Dispatches low stock alert when product total stock across all variants falls below 250 units.
        Recipients: Store Owners and Managers with low_stock_total_alerts enabled.
        """
        store = product.store
        memberships = (
            StoreMembership.objects.filter(
                store=store,
                role__in=[StoreRole.OWNER, StoreRole.MANAGER],
            )
            .select_related("user")
        )

        sent_count = 0
        subject = f"[{store.name}] Low Total Stock Alert: {product.name} ({current_stock} left)"
        context = {
            "product": product,
            "current_stock": current_stock,
            "threshold": 250,
        }

        for membership in memberships:
            member_user = membership.user
            if not member_user or not member_user.email:
                continue

            if hasattr(member_user, "settings"):
                if not member_user.settings.email_notifications or not member_user.settings.low_stock_total_alerts:
                    logger.info("Seller total low stock alert skipped for member %s per preferences", member_user.email)
                    continue

            NotificationEmailService.send_multipart_email(
                to_email=member_user.email,
                subject=subject,
                template_name="emails/seller_low_stock_total.html",
                context=context,
            )
            sent_count += 1

        return sent_count

    @staticmethod
    def send_seller_low_stock_variant_alert(product, size, current_stock):
        """
        Dispatches low stock alert when an individual variant size falls below 20 units.
        Recipients: Store Owners and Managers with low_stock_variant_alerts enabled.
        """
        store = product.store
        memberships = (
            StoreMembership.objects.filter(
                store=store,
                role__in=[StoreRole.OWNER, StoreRole.MANAGER],
            )
            .select_related("user")
        )

        sent_count = 0
        subject = f"[{store.name}] Low Stock Alert: {product.name} (Size {size}: {current_stock} left)"
        context = {
            "product": product,
            "size": size,
            "current_stock": current_stock,
            "threshold": 20,
        }

        for membership in memberships:
            member_user = membership.user
            if not member_user or not member_user.email:
                continue

            if hasattr(member_user, "settings"):
                if not member_user.settings.email_notifications or not member_user.settings.low_stock_variant_alerts:
                    logger.info("Seller variant low stock alert skipped for member %s per preferences", member_user.email)
                    continue

            NotificationEmailService.send_multipart_email(
                to_email=member_user.email,
                subject=subject,
                template_name="emails/seller_low_stock_variant.html",
                context=context,
            )
            sent_count += 1

        return sent_count

    @staticmethod
    def send_seller_low_stock_alert(product, size="", current_stock=0, threshold=5):
        """
        Dispatches low stock warning to Store Owners and Managers (legacy compatibility).
        """
        store = product.store
        memberships = (
            StoreMembership.objects.filter(
                store=store,
                role__in=[StoreRole.OWNER, StoreRole.MANAGER],
            )
            .select_related("user")
        )

        sent_count = 0
        size_label = f" ({size})" if size else ""
        subject = f"[{store.name}] Low Stock Alert: {product.name}{size_label}"
        context = {
            "product": product,
            "size": size,
            "current_stock": current_stock,
            "threshold": threshold,
        }

        for membership in memberships:
            member_user = membership.user
            if not member_user or not member_user.email:
                continue

            if hasattr(member_user, "settings"):
                if not member_user.settings.email_notifications or (
                    not member_user.settings.stock_alerts
                    and not member_user.settings.low_stock_total_alerts
                    and not member_user.settings.low_stock_variant_alerts
                ):
                    logger.info("Seller low stock alert skipped for member %s per preferences", member_user.email)
                    continue

            NotificationEmailService.send_multipart_email(
                to_email=member_user.email,
                subject=subject,
                template_name="emails/seller_low_stock.html",
                context=context,
            )
            sent_count += 1

        return sent_count
