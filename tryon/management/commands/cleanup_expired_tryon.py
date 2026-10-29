from django.core.management.base import BaseCommand
from tryon.services import TryOnService


class Command(BaseCommand):
    help = (
        "Purges Virtual Try-On requests older than 1 hour (or custom --hours), "
        "deleting uploaded customer selfie photos and AI-generated try-on result images from S3 / storage."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=1,
            help="Age threshold in hours before a try-on request and its images expire (default: 1).",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        self.stdout.write(f"Purging try-on records older than {hours} hour(s)...")

        result = TryOnService.purge_expired_requests(hours=hours)

        cleared_count = result.get("cleared_count", 0)
        deleted_images_count = result.get("deleted_images_count", 0)
        orphaned_s3 = result.get("orphaned_s3_count", 0)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully purged {cleared_count} TryOnRequest record(s), "
                f"{deleted_images_count} associated image(s) from storage, "
                f"and {orphaned_s3} orphaned S3 asset(s)."
            )
        )
