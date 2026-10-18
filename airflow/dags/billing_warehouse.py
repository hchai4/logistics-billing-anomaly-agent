from datetime import datetime, timedelta

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
DATABASE_URL = "postgresql://postgres:postgres@postgres:5432/logistics_db"


def alert_failure(context):
    task_instance = context["task_instance"]
    print(
        f"ALERT {task_instance.dag_id}.{task_instance.task_id} "
        f"failed for logical date {context.get('ds')}"
    )


def alert_sla_miss(dag, task_list, blocking_task_list, slas, blocking_tis):
    print(f"ALERT SLA missed for {[task.task_id for task in task_list]}")


with DAG(
    dag_id="billing_warehouse",
    start_date=datetime(2026, 10, 15),
    schedule="@daily",
    catchup=False,
    tags=["billing"],
    sla_miss_callback=alert_sla_miss,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
        "on_failure_callback": alert_failure,
    },
) as dag:
    seed_sources = BashOperator(
        task_id="seed_source_tables",
        bash_command="python /opt/billing/data/generate_data.py",
        env={"DATABASE_URL": DATABASE_URL, "LOGICAL_DATE": "{{ ds }}"},
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
    flag_anomalies = BashOperator(
        task_id="flag_anomalies",
        bash_command="cd /opt/billing && python -m src.analytics.anomaly_detector",
        env={"DATABASE_URL": DATABASE_URL, "PYTHONPATH": "/opt/billing"},
        append_env=True,
        trigger_rule="all_done",
        sla=timedelta(hours=1),
    )

    seed_sources >> dbt_seed >> dbt_run >> dbt_test >> flag_anomalies




    