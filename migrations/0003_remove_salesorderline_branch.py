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
            # Guard on the table existing: on a fresh DB this migration may run
            # before the `sales` app has created sales_salesorderline (it lives
            # in another module and there is no cross-app dependency). When the
            # table is absent there is nothing to clean up, so this is a no-op.
            sql="""
                DO $$
                BEGIN
                    IF to_regclass('public.sales_salesorderline') IS NOT NULL THEN
                        ALTER TABLE sales_salesorderline
                            DROP COLUMN IF EXISTS branch_id;
                    END IF;
                END $$;
            """,
            reverse_sql="""
                DO $$
                BEGIN
                    IF to_regclass('public.sales_salesorderline') IS NOT NULL THEN
                        ALTER TABLE sales_salesorderline
                            ADD COLUMN IF NOT EXISTS branch_id BIGINT
                                REFERENCES base_branch(id)
                                ON DELETE SET NULL;
                    END IF;
                END $$;
            """,
        ),
    ]
