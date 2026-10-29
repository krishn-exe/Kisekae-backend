from django.core.management.base import BaseCommand
from django.db.models import F
from products.models import Product, ProductStatus, SearchHistory, SearchSuggestion


class Command(BaseCommand):
    help = "Seeds and rebuilds the SearchSuggestion dictionary from active products and search history."

    def handle(self, *args, **options):
        self.stdout.write("Seeding SearchSuggestion dictionary...")

        count_created = 0
        count_updated = 0

        # 1. Seed from past search queries
        search_records = SearchHistory.objects.all().values("query", "search_count")
        for rec in search_records:
            q = rec["query"].strip().lower()
            if len(q) < 2:
                continue
            obj, created = SearchSuggestion.objects.get_or_create(
                query=q,
                defaults={"score": max(1, rec["search_count"])},
            )
            if created:
                count_created += 1
            else:
                if obj.score < rec["search_count"]:
                    obj.score = rec["search_count"]
                    obj.save(update_fields=["score", "updated_at"])
                    count_updated += 1

        # 2. Seed from active product titles
        active_products = Product.objects.filter(status=ProductStatus.ACTIVE).values("name", "category")
        for prod in active_products:
            name = prod["name"].strip().lower()
            cat = prod["category"] or ""
            if len(name) >= 2:
                obj, created = SearchSuggestion.objects.get_or_create(
                    query=name,
                    defaults={"score": 5, "category": cat},
                )
                if created:
                    count_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded search suggestions: {count_created} created, {count_updated} updated. "
                f"Total vocabulary size: {SearchSuggestion.objects.count()}."
            )
        )
