from django.apps import AppConfig


class CustomersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'customers'
    verbose_name = 'Customers'

    def ready(self):
        from customers.provisioning import should_start_provisioner, start_provisioner

        if should_start_provisioner():
            start_provisioner()
