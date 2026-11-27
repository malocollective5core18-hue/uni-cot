"""Re-upload existing public workbook assets as authenticated raw files."""

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import schema_context

from core.models import FounderTimetableSource
from core.storage import PrivateWorkbookCloudinaryStorage
from exams.models import FounderExamTimetableSource


class Command(BaseCommand):
    help = (
        'Move existing shared and examination workbooks from legacy public storage '
        'to authenticated raw Cloudinary storage. Defaults to a read-only inventory.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Perform uploads and database pointer updates. Without this flag, only count legacy files.',
        )

    def handle(self, *args, **options):
        storage = PrivateWorkbookCloudinaryStorage()
        if options['apply'] and not storage.is_ready():
            raise CommandError('Cloudinary credentials are required to migrate workbook assets.')

        scanned = migrated = skipped = 0
        with schema_context('public'):
            sources = []
            if FounderTimetableSource.objects.filter(pk=1).exists():
                sources.append((FounderTimetableSource, [1]))
            exam_ids = list(
                FounderExamTimetableSource.objects.exclude(file='').values_list('pk', flat=True)
            )
            sources.append((FounderExamTimetableSource, exam_ids))

            for model, source_ids in sources:
                for source_id in source_ids:
                    source = model.objects.filter(pk=source_id).first()
                    if not source or not source.file:
                        continue
                    scanned += 1
                    old_name = source.file.name
                    if storage.is_private_workbook(old_name):
                        skipped += 1
                        continue
                    if not options['apply']:
                        continue

                    original_name = source.original_filename or old_name.rsplit('/', 1)[-1]
                    new_name = None
                    try:
                        with source.file.open('rb') as legacy_file:
                            contents = legacy_file.read()
                        new_name = storage.save(original_name, ContentFile(contents, name=original_name))
                        with transaction.atomic():
                            current = model.objects.select_for_update().get(pk=source_id)
                            if current.file.name != old_name:
                                storage.delete(new_name)
                                skipped += 1
                                continue
                            current.file.name = new_name
                            current.save(update_fields=('file',))
                    except Exception:
                        if new_name:
                            storage.delete(new_name)
                        raise CommandError(
                            f'Workbook migration stopped after {migrated} successful files; '
                            f'source record {source_id} could not be copied safely.'
                        ) from None
                    try:
                        old_deleted = storage.delete(old_name)
                    except Exception:
                        old_deleted = False
                    if not old_deleted:
                        raise CommandError(
                            f'Source record {source_id} now uses private storage, but its old asset '
                            'could not be deleted. Remove the old public asset before production use.'
                        )
                    migrated += 1

        if options['apply']:
            self.stdout.write(self.style.SUCCESS(
                f'Scanned {scanned} workbook(s); migrated {migrated}; skipped {skipped}.'
            ))
        else:
            self.stdout.write(
                f'Dry run: found {scanned} workbook(s), {skipped} already private. '
                'Review the configured database and Cloudinary account, then pass --apply to migrate.'
            )
