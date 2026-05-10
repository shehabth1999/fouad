from django.db import migrations


class Migration(migrations.Migration):
    """
    Drop the per-line branch_id column that was added by
    SalesOrderLineElFouadExtension in a previous version.
    Branch is now handled at the parent SalesOrder level only.
    """

    dependencies = [
        ('el_fouad', '0002_alfouadapiconfig'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE sales_salesorderline
                DROP COLUMN IF EXISTS branch_id;
            """,
            reverse_sql="""
                ALTER TABLE sales_salesorderline
                ADD COLUMN IF NOT EXISTS branch_id BIGINT
                    REFERENCES base_branch(id)
                    ON DELETE SET NULL;
            """,
        ),
    ]
