from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

DBT = "/opt/airflow/dbt-venv/bin/dbt"
DBT_ENV = {
    "POSTGRES_HOST": "postgres",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "postgres",
    "POSTGRES_DB": "logistics_db",
    "DBT_PROFILES_DIR": "/opt/dbt",
}

with DAG(
    dag_id="billing_warehouse",
    start_date=datetime(2026, 10, 15),
    schedule="@daily",
    catchup=False,
    tags=["billing"],
) as dag:
    seed_sources = BashOperator(
        task_id="seed_source_tables",
        bash_command="python /opt/billing/data/generate_data.py",
        env={
            "DATABASE_URL": "postgresql://postgres:postgres@postgres:5432/logistics_db",
        },
        append_env=True,
    )
    dbt_seed = BashOperator(
        task_id="dbt_seed",
        bash_command=f"cd /opt/dbt && {DBT} seed",
        env=DBT_ENV,
        append_env=True,
    )
    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=f"cd /opt/dbt && {DBT} run",
        env=DBT_ENV,
        append_env=True,
    )
    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"cd /opt/dbt && {DBT} test",
        env=DBT_ENV,
        append_env=True,
    )

    seed_sources >> dbt_seed >> dbt_run >> dbt_test