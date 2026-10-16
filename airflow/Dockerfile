FROM apache/airflow:2.10.5-python3.12

USER airflow
RUN pip install --no-cache-dir psycopg2-binary python-dotenv \
    && python -m venv /opt/airflow/dbt-venv \
    && /opt/airflow/dbt-venv/bin/pip install --no-cache-dir "dbt-core<2" "dbt-postgres<2"