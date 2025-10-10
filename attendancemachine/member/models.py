from django.db import models

class Member(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    position = models.CharField(max_length=255)

    class Meta:
        db_table = 'member'  # table name
        ordering = ['id']

    def __str__(self):
        return f'{self.name} <{self.email}>'