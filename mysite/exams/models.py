from django.db import models
from core.storage import PrivateWorkbookCloudinaryStorage


class FounderExamTimetableSource(models.Model):
    TEST1 = 'test1'
    TEST2 = 'test2'
    UE = 'ue'
    TYPE_CHOICES = (
        (TEST1, 'Test 1'),
        (TEST2, 'Test 2'),
        (UE, 'UE'),
    )

    type = models.CharField(max_length=10, choices=TYPE_CHOICES, unique=True)
    file = models.FileField(
        upload_to='system/exam_timetables/',
        storage=PrivateWorkbookCloudinaryStorage(),
        blank=True,
    )
    original_filename = models.CharField(max_length=255, blank=True)
    version = models.PositiveIntegerField(default=0)
    uploaded_by_id = models.BigIntegerField(blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'exams_founderexam_source'
        ordering = ('type',)

    def __str__(self):
        return f'{self.get_type_display()} timetable v{self.version}'
