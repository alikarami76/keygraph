from django.db import models


class Payment(models.Model):
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, db_column="gateway_ref")
    order = models.ForeignKey("Order", on_delete=models.CASCADE)

    class Meta:
        db_table = "payments"


class Draft(models.Model):
    status = models.IntegerField()
