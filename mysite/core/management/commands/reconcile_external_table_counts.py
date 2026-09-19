from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import ExternalTable


class Command(BaseCommand):
    help = "Reconcile stored external-table record counts with the database."

    def handle(self, *args, **options):
        corrected = 0
        tables = ExternalTable.objects.annotate(actual_count=Count("records"))
        for table in tables.iterator():
            if table.record_count != table.actual_count:
                ExternalTable.objects.filter(pk=table.pk).update(record_count=table.actual_count)
                corrected += 1
        self.stdout.write(self.style.SUCCESS(f"Reconciled {corrected} external table count(s)."))
